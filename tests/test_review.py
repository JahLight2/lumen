"""Review slice: timestamped comments, accountless links, selects, marker export.

These run against the real HTTP server. They prove the *rules* (scoping, expiry,
revocation, version binding, no folder leakage) — not that any NLE imports the
marker CSV, which remains an untested integration.
"""

import csv
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from lfmc.core import db, review, scanner
from lfmc.net.server import Config, make_server
from lfmc.spikes.mp4_remote_open import write_synthetic_mp4

TOKEN = "review-test-token-xyz"


class ReviewFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.drive = cls.tmp / "drive"
        (cls.drive / "01_DAY1").mkdir(parents=True)
        (cls.drive / "SECRET_CLIENT").mkdir(parents=True)
        write_synthetic_mp4(str(cls.drive / "01_DAY1" / "interview.mp4"), media_bytes=2 << 20)
        (cls.drive / "SECRET_CLIENT" / "unrelated_deal.txt").write_text("confidential")
        cls.db_path = cls.tmp / "catalogue.db"
        conn = db.connect(cls.db_path)
        cls.root_id = scanner.register_root(conn, str(cls.drive), "Camera 01")
        scanner.scan_root(conn, cls.root_id)
        cls.asset = conn.execute("SELECT * FROM assets WHERE name='interview.mp4'").fetchone()
        cls.secret = conn.execute("SELECT * FROM assets WHERE name='unrelated_deal.txt'").fetchone()
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

    def req(self, path, method="GET", body=None, headers=None, auth=True):
        hdrs = {"Authorization": f"Bearer {TOKEN}"} if auth else {}
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                   data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def conn(self):
        return db.connect(self.db_path)


class CommentTests(ReviewFixture):
    def test_comment_with_timecode_is_frame_accurate(self):
        code, _, body = self.req(f"/api/assets/{self.asset['id']}/comments", "POST",
                                 {"body": "cut here", "author": "producer",
                                  "timecode_ms": 12345, "fps": 25})
        self.assertEqual(code, 201, body[:300])
        c = json.loads(body)
        self.assertEqual(c["timecode"], review.timecode(12345, 25))
        self.assertEqual(c["fps"], 25)

    def test_timecode_rendering_and_parsing(self):
        self.assertEqual(review.timecode(0, 25), "00:00:00:00")
        self.assertEqual(review.timecode(1000, 25), "00:00:01:00")
        # 25 fps: frame boundaries land every 40 ms, so use an exact frame
        self.assertEqual(review.timecode(1520, 25), "00:00:01:13")
        self.assertEqual(review.parse_timecode("00:00:01:13", 25), 1520)
        self.assertEqual(review.parse_timecode("1:30", 25), 90000)
        self.assertEqual(review.parse_timecode("90", 25), 90000)
        with self.assertRaises(review.ReviewError):
            review.parse_timecode("not-a-time")

    def test_empty_comment_rejected(self):
        code, _, _ = self.req(f"/api/assets/{self.asset['id']}/comments", "POST",
                              {"body": "   ", "author": "x"})
        self.assertEqual(code, 400)

    def test_reply_stays_on_the_same_version(self):
        _, _, body = self.req(f"/api/assets/{self.asset['id']}/comments", "POST",
                              {"body": "v1 note", "author": "a", "version_label": "v1"})
        parent = json.loads(body)
        code, _, _ = self.req(f"/api/assets/{self.asset['id']}/comments", "POST",
                              {"body": "late reply", "author": "b", "parent_id": parent["id"],
                               "version_label": "v2"})
        self.assertEqual(code, 400, "a reply must not be attachable to a different cut")

    def test_resolve_and_react(self):
        _, _, body = self.req(f"/api/assets/{self.asset['id']}/comments", "POST",
                              {"body": "fix audio", "author": "a"})
        cid = json.loads(body)["id"]
        code, _, body = self.req(f"/api/comments/{cid}/resolve", "POST", {"resolved": True})
        self.assertEqual(code, 200)
        self.assertIsNotNone(json.loads(body)["resolved_at"])
        code, _, body = self.req(f"/api/comments/{cid}/react", "POST",
                                 {"emoji": "🙌", "actor": "b"})
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)["reactions"]), 1)
        # toggling the same reaction removes it
        _, _, body = self.req(f"/api/comments/{cid}/react", "POST",
                              {"emoji": "🙌", "actor": "b"})
        self.assertEqual(len(json.loads(body)["reactions"]), 0)


class ReviewLinkTests(ReviewFixture):
    def _create(self, **kw):
        body = {"asset_id": self.asset["id"], "expires_in_hours": 24}
        body.update(kw)
        code, _, raw = self.req("/api/review-links", "POST", body)
        self.assertEqual(code, 201, raw[:300])
        return json.loads(raw)

    def test_guest_can_read_and_comment_without_account(self):
        link = self._create(passphrase="open-sesame")
        t = link["token"]
        code, _, raw = self.req(f"/api/guest/{t}", headers={"X-LFMC-Passphrase": "open-sesame"},
                                auth=False)
        self.assertEqual(code, 200, raw[:300])
        data = json.loads(raw)
        self.assertEqual(data["asset"]["id"], self.asset["id"])

        code, _, raw = self.req(f"/api/guest/{t}/comments",
                                "POST",
                                {"body": "Love the grade", "author": "Client Jane",
                                 "timecode_ms": 4000},
                                headers={"X-LFMC-Passphrase": "open-sesame"}, auth=False)
        self.assertEqual(code, 201, raw[:300])
        c = json.loads(raw)
        self.assertEqual(c["author_kind"], "guest")
        self.assertEqual(c["visibility"], "shared")

    def test_passphrase_required_then_enforced(self):
        link = self._create(passphrase="correct horse")
        t = link["token"]
        code, _, raw = self.req(f"/api/guest/{t}", auth=False)
        self.assertEqual(code, 401)
        self.assertEqual(json.loads(raw)["error"], "passphrase_required")
        code, _, raw = self.req(f"/api/guest/{t}", headers={"X-LFMC-Passphrase": "wrong"},
                                auth=False)
        self.assertEqual(code, 401)
        self.assertEqual(json.loads(raw)["error"], "passphrase_invalid")

    def test_guest_never_sees_internal_comments(self):
        self.req(f"/api/assets/{self.asset['id']}/comments", "POST",
                 {"body": "INTERNAL: don't show the client", "author": "producer",
                  "visibility": "internal"})
        self.req(f"/api/assets/{self.asset['id']}/comments", "POST",
                 {"body": "client-visible note", "author": "producer",
                  "visibility": "shared"})
        link = self._create()
        code, _, raw = self.req(f"/api/guest/{link['token']}", auth=False)
        bodies = [c["body"] for c in json.loads(raw)["comments"]]
        self.assertNotIn("INTERNAL: don't show the client", bodies)
        self.assertIn("client-visible note", bodies)

    def test_link_is_scoped_and_cannot_reveal_other_assets(self):
        link = self._create()
        code, _, raw = self.req(f"/api/guest/{link['token']}", auth=False)
        payload = json.dumps(json.loads(raw))
        self.assertNotIn("unrelated_deal", payload)
        self.assertNotIn("SECRET_CLIENT", payload)
        self.assertNotIn("Camera 01", payload, "guest payload must not expose root labels")

    def test_revoked_link_is_dead(self):
        link = self._create()
        code, _, _ = self.req(f"/api/review-links/{link['id']}/revoke", "POST", {"actor": "owner"})
        self.assertEqual(code, 200)
        code, _, raw = self.req(f"/api/guest/{link['token']}", auth=False)
        self.assertEqual(code, 410)
        self.assertEqual(json.loads(raw)["error"], "revoked")

    def test_expired_link_is_dead(self):
        link = self._create()
        conn = self.conn()
        conn.execute("UPDATE review_links SET expires_at=? WHERE id=?",
                     ("2020-01-01T00:00:00+00:00", link["id"]))
        conn.commit()
        conn.close()
        code, _, raw = self.req(f"/api/guest/{link['token']}", auth=False)
        self.assertEqual(code, 410)
        self.assertEqual(json.loads(raw)["error"], "expired")

    def test_token_is_hashed_at_rest_and_never_echoed(self):
        link = self._create()
        conn = self.conn()
        row = conn.execute("SELECT * FROM review_links WHERE id=?", (link["id"],)).fetchone()
        conn.close()
        self.assertNotEqual(row["token_hash"], link["token"])
        self.assertEqual(len(row["token_hash"]), 64)
        code, _, raw = self.req("/api/review-links")
        self.assertNotIn(link["token"], raw.decode())
        self.assertNotIn("token_hash", raw.decode())

    def test_comments_can_be_disabled_on_a_link(self):
        link = self._create(allow_comments=False)
        code, _, raw = self.req(f"/api/guest/{link['token']}/comments", "POST",
                                {"body": "nope", "author": "x"}, auth=False)
        self.assertEqual(code, 403)


class SelectAndMarkerTests(ReviewFixture):
    def test_select_persists_and_updates(self):
        code, _, raw = self.req(f"/api/assets/{self.asset['id']}/select", "POST",
                                {"decision": "keep", "reviewer": "producer"})
        self.assertEqual(code, 200, raw[:200])
        code, _, raw = self.req(f"/api/assets/{self.asset['id']}/select", "POST",
                                {"decision": "maybe", "reviewer": "producer"})
        self.assertEqual(json.loads(raw)["decision"], "maybe")
        _, _, raw = self.req("/api/selects")
        rows = json.loads(raw)["selects"]
        self.assertEqual(len([r for r in rows if r["asset_id"] == self.asset["id"]]), 1,
                         "one reviewer holds one decision per asset")

    def test_invalid_decision_rejected(self):
        code, _, _ = self.req(f"/api/assets/{self.asset['id']}/select", "POST",
                              {"decision": "favourite", "reviewer": "p"})
        self.assertEqual(code, 400)

    def test_marker_csv_exports_timecoded_notes(self):
        self.req(f"/api/assets/{self.asset['id']}/comments", "POST",
                 {"body": "tighten this cut, please", "author": "client",
                  "timecode_ms": 1520, "fps": 25})
        code, headers, raw = self.req(f"/api/assets/{self.asset['id']}/markers.csv")
        self.assertEqual(code, 200)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertEqual(headers.get("X-LFMC-Export-Verified"), "no",
                         "must not claim NLE round-trip before it is tested")
        rows = list(csv.DictReader(io.StringIO(raw.decode())))
        self.assertTrue(rows)
        row = [r for r in rows if r["Description"].startswith("tighten")][0]
        self.assertEqual(row["Timecode"], "00:00:01:13")
        self.assertEqual(row["Frame"], "38")

    def test_markers_require_auth(self):
        code, _, _ = self.req(f"/api/assets/{self.asset['id']}/markers.csv", auth=False)
        self.assertEqual(code, 401)


class GuestPageTests(ReviewFixture):
    def test_guest_page_is_served_without_auth_and_carries_no_data(self):
        code, headers, body = self.req("/r/some-token-value", auth=False)
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers["Content-Type"])
        text = body.decode()
        self.assertIn("Lumen review", text)
        self.assertNotIn("unrelated_deal", text)
        self.assertNotIn("Camera 01", text)


if __name__ == "__main__":
    unittest.main()
