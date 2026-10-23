"""Read-only WebDAV façade: protocol behaviour, safety, and offline semantics.

These tests exercise the real HTTP server. They do NOT prove that macOS Finder,
the Windows WebDAV redirector, or an NLE can mount it — that is a real-machine
test (docs/WEBDAV_MOUNT.md, docs/NLE_TEST_LOG.md).
"""

import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from lfmc.core import db, scanner
from lfmc.net.server import Config, make_server
from lfmc.spikes.mp4_remote_open import write_synthetic_mp4

TOKEN = "dav-test-token-abcdefgh"
AMHARIC_NAME = "A001 ፊልም clip.mp4"


class DavFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.drive = cls.tmp / "drive"
        (cls.drive / "01_DAY1").mkdir(parents=True)
        (cls.drive / "02_DRONE").mkdir(parents=True)
        write_synthetic_mp4(str(cls.drive / "01_DAY1" / AMHARIC_NAME), media_bytes=2 << 20)
        (cls.drive / "01_DAY1" / "notes.txt").write_text("two takes, lav 1")
        write_synthetic_mp4(str(cls.drive / "02_DRONE" / "DJI_0007.mp4"), media_bytes=1 << 20)
        write_synthetic_mp4(str(cls.drive / "02_DRONE" / "DJI_0008_large.mp4"),
                            media_bytes=10 << 20)
        cls.db_path = cls.tmp / "catalogue.db"
        conn = db.connect(cls.db_path)
        cls.root_id = scanner.register_root(conn, str(cls.drive), "Camera 01")
        scanner.scan_root(conn, cls.root_id)
        cls.amharic = conn.execute("SELECT * FROM assets WHERE name=?",
                                   (AMHARIC_NAME,)).fetchone()
        conn.close()

        cls.config = Config(db_path=str(cls.db_path), token=TOKEN, host="127.0.0.1", port=0)
        cls.httpd = make_server(cls.config)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def req(self, path, method="GET", headers=None, auth=True, basic=False):
        hdrs = {}
        if auth:
            if basic:
                raw = base64.b64encode(f"editor:{TOKEN}".encode()).decode()
                hdrs["Authorization"] = f"Basic {raw}"
            else:
                hdrs["Authorization"] = f"Bearer {TOKEN}"
        hdrs.update(headers or {})
        url = f"http://127.0.0.1:{self.port}{path}"
        request = urllib.request.Request(url, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def dav(self, *segments):
        return "/dav/" + "/".join(urllib.parse.quote(s, safe="") for s in segments)

    def conn(self):
        return db.connect(self.db_path)


class CapabilityTests(DavFixture):
    def test_options_advertises_webdav(self):
        code, headers, _ = self.req("/dav/", method="OPTIONS", auth=False)
        self.assertEqual(code, 200)
        self.assertIn("1", headers.get("DAV", ""))
        allowed = headers.get("Allow", "")
        for verb in ("GET", "HEAD", "PROPFIND"):
            self.assertIn(verb, allowed)
        self.assertNotIn("PUT", allowed)

    def test_dav_info_endpoint_states_limits(self):
        code, _, body = self.req("/api/dav/info")
        self.assertEqual(code, 200)
        info = json.loads(body)
        self.assertIn("read-only", info["protocol"])
        self.assertIn("503", info["offline_semantics"])


class AuthTests(DavFixture):
    def test_basic_auth_with_token_as_password(self):
        code, _, body = self.req("/dav/", method="PROPFIND", basic=True)
        self.assertEqual(code, 207, body[:200])

    def test_wrong_basic_password_gets_challenge(self):
        headers = {"Authorization": "Basic " + base64.b64encode(b"editor:wrong").decode()}
        code, resp_headers, _ = self.req("/dav/", method="PROPFIND",
                                         headers=headers, auth=False)
        self.assertEqual(code, 401)
        self.assertIn("Basic", resp_headers.get("WWW-Authenticate", ""))

    def test_unauthenticated_propfind_is_401(self):
        code, headers, _ = self.req("/dav/", method="PROPFIND", auth=False)
        self.assertEqual(code, 401)
        self.assertIn("WWW-Authenticate", headers)


class PropfindTests(DavFixture):
    def test_depth0_describes_only_the_collection_itself(self):
        code, headers, body = self.req("/dav/", method="PROPFIND", headers={"Depth": "0"})
        self.assertEqual(code, 207)
        self.assertTrue(headers["Content-Type"].startswith("application/xml"))
        text = body.decode()
        self.assertIn("<D:collection/>", text)
        self.assertNotIn("Camera 01", text, "Depth 0 must not enumerate children")

    def test_depth1_lists_roots(self):
        # Finder and the Windows redirector both probe with Depth: 1.
        code, _, body = self.req("/dav/", method="PROPFIND", headers={"Depth": "1"})
        self.assertEqual(code, 207)
        text = body.decode()
        self.assertIn("Camera 01", text)
        self.assertIn("/dav/Camera%2001/", text)

    def test_depth1_lists_folders_and_files(self):
        code, _, body = self.req(self.dav("Camera 01"), method="PROPFIND",
                                 headers={"Depth": "1"})
        text = body.decode()
        self.assertEqual(code, 207)
        self.assertIn("01_DAY1", text)
        self.assertIn("02_DRONE", text)
        # files live one level down, so their props appear in that listing
        code, _, body = self.req(self.dav("Camera 01", "02_DRONE"), method="PROPFIND",
                                 headers={"Depth": "1"})
        text = body.decode()
        self.assertIn("DJI_0007.mp4", text)
        self.assertIn("getcontentlength", text)

    def test_file_props_include_size_and_etag(self):
        code, _, body = self.req(self.dav("Camera 01", "01_DAY1", "notes.txt"),
                                 method="PROPFIND", headers={"Depth": "0"})
        text = body.decode()
        self.assertEqual(code, 207)
        size = (self.drive / "01_DAY1" / "notes.txt").stat().st_size
        self.assertIn(f"<D:getcontentlength>{size}</D:getcontentlength>", text)
        self.assertIn("getetag", text)
        self.assertIn("text/plain", text)

    def test_depth_infinity_is_capped_at_one(self):
        code, _, body = self.req(self.dav("Camera 01"), method="PROPFIND",
                                 headers={"Depth": "infinity"})
        text = body.decode()
        self.assertEqual(code, 207)
        self.assertIn("01_DAY1", text)
        self.assertNotIn("notes.txt", text,
                         "recursive walks must be capped, not follow the whole drive")

    def test_child_hrefs_are_anchored_at_the_root_segment(self):
        """A dropped root segment makes a mount's hrefs resolve to nothing."""
        code, _, body = self.req(self.dav("Camera 01", "02_DRONE"), method="PROPFIND",
                                 headers={"Depth": "1"})
        hrefs = __import__("re").findall(r"<D:href>([^<]+)</D:href>", body.decode())
        self.assertEqual(code, 207)
        self.assertGreaterEqual(len(hrefs), 2)
        for href in hrefs:
            self.assertTrue(href.startswith("/dav/Camera%2001/"),
                            f"href escaped its root: {href}")
        # and each href must resolve back to the resource it names
        for href in hrefs[1:]:
            code, _, _ = self.req(href, method="PROPFIND", headers={"Depth": "0"})
            self.assertEqual(code, 207, href)

    def test_unknown_root_is_404(self):
        code, _, _ = self.req(self.dav("Not A Drive"), method="PROPFIND",
                              headers={"Depth": "0"})
        self.assertEqual(code, 404)

    def test_traversal_is_rejected(self):
        for attempt in ("/dav/Camera%2001/../../../etc/passwd",
                        "/dav/Camera%2001/..%2f..%2fetc%2fpasswd"):
            code, _, body = self.req(attempt, method="PROPFIND", headers={"Depth": "0"})
            self.assertIn(code, (400, 404), attempt)
            self.assertNotIn(b"root:x:", body)

    def test_amharic_and_spaces_round_trip(self):
        code, _, body = self.req(self.dav("Camera 01", "01_DAY1"), method="PROPFIND",
                                 headers={"Depth": "1"})
        text = body.decode()
        self.assertEqual(code, 207)
        self.assertIn(urllib.parse.quote(AMHARIC_NAME, safe=""), text,
                      "hrefs must be percent-encoded")
        href = self.dav("Camera 01", "01_DAY1", AMHARIC_NAME)
        code, headers, _ = self.req(href, method="HEAD")
        self.assertEqual(code, 200, href)
        self.assertEqual(int(headers["Content-Length"]), self.amharic["size_bytes"])


class ReadTests(DavFixture):
    def test_ranged_get_returns_exact_bytes(self):
        href = self.dav("Camera 01", "02_DRONE", "DJI_0007.mp4")
        code, headers, body = self.req(href, headers={"Range": "bytes=0-31"})
        self.assertEqual(code, 206)
        self.assertEqual(len(body), 32)
        self.assertEqual(headers["Content-Range"],
                         f"bytes 0-31/{headers['Content-Range'].split('/')[1]}")

    def test_small_file_without_range_is_served(self):
        href = self.dav("Camera 01", "01_DAY1", "notes.txt")
        code, _, body = self.req(href)
        self.assertEqual(code, 200)
        self.assertEqual(body, b"two takes, lav 1")

    def test_large_file_without_range_is_refused(self):
        href = self.dav("Camera 01", "02_DRONE", "DJI_0008_large.mp4")
        code, _, body = self.req(href)
        self.assertEqual(code, 400, "a >8 MB original must never be streamed whole")
        self.assertEqual(json.loads(body)["error"], "range_required")

    def test_browser_listing_of_collection(self):
        code, headers, body = self.req(self.dav("Camera 01"))
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"Read-only WebDAV", body)


class ReadOnlyTests(DavFixture):
    def test_put_is_refused_and_writes_nothing(self):
        target = self.drive / "01_DAY1" / "injected.txt"
        href = self.dav("Camera 01", "01_DAY1", "injected.txt")
        payload = b"should never land on the drive"
        code, _, body = self.req(href, method="PUT", headers={"Content-Length": str(len(payload))})
        self.assertEqual(code, 405)
        self.assertEqual(json.loads(body)["error"], "read_only")
        self.assertFalse(target.exists(), "source media must never be modified or added to")

    def test_delete_mkcol_proppatch_refused(self):
        href = self.dav("Camera 01", "01_DAY1", "notes.txt")
        for method in ("DELETE", "MKCOL", "PROPPATCH"):
            code, _, _ = self.req(href, method=method)
            self.assertEqual(code, 405, method)
        self.assertTrue((self.drive / "01_DAY1" / "notes.txt").exists())

    def test_lock_is_not_faked(self):
        code, _, body = self.req(self.dav("Camera 01", "01_DAY1", "notes.txt"), method="LOCK")
        self.assertEqual(code, 501)
        self.assertEqual(json.loads(body)["error"], "locks_unsupported")


class OfflineTests(DavFixture):
    def test_offline_original_returns_503_but_listings_work(self):
        conn = self.conn()
        scanner.pause_sharing(conn, self.root_id)
        conn.close()
        try:
            href = self.dav("Camera 01", "02_DRONE", "DJI_0007.mp4")
            code, _, body = self.req(href, headers={"Range": "bytes=0-9"})
            self.assertEqual(code, 503, "an offline host must look temporary, not deleted")
            payload = json.loads(body)
            self.assertEqual(payload["error"], "original_unavailable")
            self.assertIn("offline", payload["message"])

            code, _, listing = self.req(self.dav("Camera 01", "02_DRONE"),
                                        method="PROPFIND", headers={"Depth": "1"})
            self.assertEqual(code, 207)
            self.assertIn(b"DJI_0007.mp4", listing,
                          "the catalogue must stay browsable while the drive is offline")
        finally:
            conn = self.conn()
            scanner.resume_sharing(conn, self.root_id)
            conn.close()


if __name__ == "__main__":
    unittest.main()
