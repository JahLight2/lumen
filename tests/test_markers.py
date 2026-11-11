"""Slice B: note -> NLE marker export, idempotent.

The rule under test: a reviewer who adds a note on Thursday must not produce a
second marker when the editor re-imports on Friday. Re-export updates the ledger
and keeps the emitted marker name stable.

What is NOT tested here, and must not be claimed: that Premiere Pro or DaVinci
Resolve actually import these files. No NLE runs in this environment. The CSV and
TSV layouts are documentation-derived and marked unverified everywhere they are
served (X-LFMC-Export-Verified: no).
"""

import csv
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from lfmc.core import db, review, scanner
from lfmc.net.server import Config, make_server
from lfmc.spikes.mp4_remote_open import write_synthetic_mp4

TOKEN = "marker-test-token"


class MarkerFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.drive = cls.tmp / "drive"
        cls.drive.mkdir(parents=True)
        write_synthetic_mp4(str(cls.drive / "interview.mp4"), media_bytes=1 << 20)
        cls.db_path = cls.tmp / "catalogue.db"
        conn = db.connect(cls.db_path)
        cls.root_id = scanner.register_root(conn, str(cls.drive), "Camera 01")
        scanner.scan_root(conn, cls.root_id)
        cls.asset_id = conn.execute(
            "SELECT id FROM assets WHERE name='interview.mp4'").fetchone()["id"]
        # three notes, two of them timecoded, one resolved
        cls.c1 = review.add_comment(conn, asset_id=cls.asset_id, body="clip the head",
                                    author="Abebe", timecode_ms=2000)
        cls.c2 = review.add_comment(conn, asset_id=cls.asset_id, body="audio drop",
                                    author="Tigist", timecode_ms=8000)
        cls.c3 = review.add_comment(conn, asset_id=cls.asset_id, body="general note",
                                    author="Marta", timecode_ms=None)
        review.resolve_comment(conn, cls.c2["id"], True, "Abebe")
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

    def conn(self):
        return db.connect(self.db_path)

    def get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)


class TestExportContents(MarkerFixture):
    def test_premiere_csv_has_one_row_per_timecoded_note(self):
        conn = self.conn()
        try:
            text = review.export_markers(conn, self.asset_id, fmt="premiere")["text"]
        finally:
            conn.close()
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0][0], "Name")
        # c1 and c2 have timecodes; c3 does not and must not become a marker
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(r) == 7 for r in rows))

    def test_resolve_tsv_is_tab_separated_with_six_columns(self):
        conn = self.conn()
        try:
            text = review.export_markers(conn, self.asset_id, fmt="resolve")["text"]
        finally:
            conn.close()
        lines = [l for l in text.splitlines() if l.strip()]
        self.assertEqual(lines[0].split("\t")[0], "Marker Name")
        for line in lines[1:]:
            self.assertEqual(len(line.split("\t")), 6)
        self.assertIn("Green", text)   # the resolved note
        self.assertIn("Blue", text)    # the open note

    def test_tabs_and_newlines_are_flattened_not_broken(self):
        conn = self.conn()
        try:
            c = review.add_comment(conn, asset_id=self.asset_id,
                                   body="line one\nline two\twith tab",
                                   author="Abebe", timecode_ms=1000)
            text = review.export_markers(conn, self.asset_id, fmt="resolve")["text"]
        finally:
            conn.close()
        self.assertEqual(len([l for l in text.splitlines() if l.strip()]), 4)
        self.assertNotIn("\twith tab", text)

    def test_unknown_format_is_rejected(self):
        conn = self.conn()
        try:
            with self.assertRaises(review.ReviewError):
                review.export_markers(conn, self.asset_id, fmt="finalcut")
        finally:
            conn.close()


class TestIdempotency(MarkerFixture):
    def test_second_export_does_not_add_markers(self):
        conn = self.conn()
        try:
            first = review.export_markers(conn, self.asset_id, fmt="premiere")
            second = review.export_markers(conn, self.asset_id, fmt="premiere")
            self.assertEqual(first["markers"], second["markers"])
            self.assertEqual(first["text"].count("\n"), second["text"].count("\n"))
            ledger = review.export_summary(conn, self.asset_id)
            premiere = [r for r in ledger if r["target"] == "premiere"]
            self.assertEqual(len(premiere), 2)                     # one row per note
            self.assertTrue(all(r["export_count"] >= 2 for r in premiere))
        finally:
            conn.close()

    def test_marker_name_is_stable_across_exports(self):
        conn = self.conn()
        try:
            first = review.export_markers(conn, self.asset_id, fmt="premiere")
            name_before = list(csv.reader(io.StringIO(first["text"])))[1][0]
            review.export_markers(conn, self.asset_id, fmt="premiere")
            third = review.export_markers(conn, self.asset_id, fmt="premiere")
            name_after = list(csv.reader(io.StringIO(third["text"])))[1][0]
            self.assertEqual(name_before, name_after)
        finally:
            conn.close()

    def test_editing_a_note_updates_text_without_changing_its_marker_name(self):
        conn = self.conn()
        try:
            before = list(csv.reader(io.StringIO(
                review.export_markers(conn, self.asset_id, fmt="premiere")["text"])))
            conn.execute("UPDATE asset_comments SET body=? WHERE id=?",
                         ("reworded note", self.c1["id"]))
            conn.commit()
            after = list(csv.reader(io.StringIO(
                review.export_markers(conn, self.asset_id, fmt="premiere")["text"])))
            self.assertEqual(before[1][0], after[1][0])            # same marker name
            self.assertEqual(after[1][4], "reworded note")          # new text
        finally:
            conn.close()

    def test_targets_are_tracked_separately(self):
        conn = self.conn()
        try:
            review.export_markers(conn, self.asset_id, fmt="premiere")
            review.export_markers(conn, self.asset_id, fmt="resolve")
            ledger = review.export_summary(conn, self.asset_id)
            targets = {r["target"] for r in ledger}
            self.assertEqual(targets, {"premiere", "resolve"})
        finally:
            conn.close()

    def test_dry_run_records_nothing(self):
        conn = self.conn()
        try:
            review.export_markers(conn, self.asset_id, fmt="premiere", dry_run=True)
            self.assertEqual(review.export_summary(conn, self.asset_id), [])
        finally:
            conn.close()


class TestExportOverHTTP(MarkerFixture):
    def test_csv_route_still_works_and_is_marked_unverified(self):
        status, body, headers = self.get(f"/api/assets/{self.asset_id}/markers.csv")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-LFMC-Export-Verified"), "no")
        self.assertIn(b"Name,Timecode", body)

    def test_format_parameter_switches_layout(self):
        status, body, headers = self.get(
            f"/api/assets/{self.asset_id}/markers?format=resolve")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-LFMC-Format"), "resolve")
        self.assertIn(b"Marker Name\tDescription", body)

    def test_bad_format_is_400_not_a_traceback(self):
        status, body, _ = self.get(f"/api/assets/{self.asset_id}/markers?format=otio")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_export")

    def test_json_view_reports_verification_false(self):
        status, body, _ = self.get(
            f"/api/assets/{self.asset_id}/markers?format=premiere&json=1")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["nle_verified"])
        self.assertEqual(payload["markers"], 2)

    def test_unknown_asset_is_404_not_500(self):
        status, body, _ = self.get("/api/assets/does-not-exist/markers?format=premiere")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
