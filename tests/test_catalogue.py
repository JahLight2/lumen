"""The core acceptance criterion: the catalogue survives a disconnected drive,
and a disconnected drive is never mistaken for deletion."""

import tempfile
import unittest
from pathlib import Path

from lfmc.core import catalogue, db, scanner
from lfmc.core.db import now_iso
from lfmc.spikes.mp4_remote_open import write_synthetic_mp4


class CatalogueFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.drive = self.tmp / "drive"
        (self.drive / "01_DAY1").mkdir(parents=True)
        (self.drive / "02_AUDIO").mkdir(parents=True)
        write_synthetic_mp4(str(self.drive / "01_DAY1" / "clip_a.mp4"), media_bytes=1 << 20)
        write_synthetic_mp4(str(self.drive / "01_DAY1" / "clip_b.mp4"), media_bytes=2 << 20)
        (self.drive / "02_AUDIO" / "interview_notes.txt").write_text("lav 1, two takes, drone B-roll")
        self.conn = db.connect(self.tmp / "catalogue.db")
        self.root_id = scanner.register_root(self.conn, str(self.drive), "Unit Test Drive")
        self.first_scan = scanner.scan_root(self.conn, self.root_id)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def assets(self):
        return catalogue.list_assets(self.conn, self.root_id, limit=100)


class ScanTests(CatalogueFixture):
    def test_first_scan_indexes_everything(self):
        self.assertEqual(self.first_scan.status, "ok")
        self.assertEqual(self.first_scan.added, 3)
        self.assertEqual(len(self.assets()), 3)
        kinds = {a["name"]: a["media_kind"] for a in self.assets()}
        self.assertEqual(kinds["clip_a.mp4"], "video")
        self.assertEqual(kinds["interview_notes.txt"], "sidecar")

    def test_second_scan_is_incremental_noop(self):
        res = scanner.scan_root(self.conn, self.root_id)
        self.assertEqual((res.added, res.changed, res.moved, res.removed), (0, 0, 0, 0))
        self.assertEqual(res.unchanged, 3)

    def test_asset_ids_are_stable_across_rescan(self):
        before = {a["rel_path"]: a["id"] for a in self.assets()}
        scanner.scan_root(self.conn, self.root_id)
        after = {a["rel_path"]: a["id"] for a in self.assets()}
        self.assertEqual(before, after)

    def test_new_file_is_added(self):
        write_synthetic_mp4(str(self.drive / "01_DAY1" / "clip_c.mp4"), media_bytes=1 << 20)
        res = scanner.scan_root(self.conn, self.root_id)
        self.assertEqual(res.added, 1)

    def test_online_deletion_is_tombstoned(self):
        (self.drive / "02_AUDIO" / "interview_notes.txt").unlink()
        res = scanner.scan_root(self.conn, self.root_id)
        self.assertEqual(res.removed, 1)
        row = self.conn.execute(
            "SELECT * FROM assets WHERE name='interview_notes.txt'").fetchone()
        self.assertIsNotNone(row["deleted_at"])
        self.assertEqual(len(self.assets()), 2)
        # tombstoned assets leave the search index
        hits = catalogue.search(self.conn, "interview")
        self.assertEqual(hits, [])


class MoveDetectionTests(CatalogueFixture):
    def test_rename_keeps_asset_id_and_path_history(self):
        before = self.conn.execute(
            "SELECT id, rel_path FROM assets WHERE name='clip_a.mp4'").fetchone()
        src = self.drive / "01_DAY1" / "clip_a.mp4"
        dst = self.drive / "02_AUDIO" / "clip_a_renamed.mp4"
        src.rename(dst)

        res = scanner.scan_root(self.conn, self.root_id)
        self.assertEqual(res.moved, 1, "a rename must be detected as a move")
        self.assertEqual(res.added, 0)

        after = self.conn.execute("SELECT * FROM assets WHERE id=?", (before["id"],)).fetchone()
        self.assertIsNotNone(after, "asset id must survive a rename")
        self.assertEqual(after["rel_path"], "02_AUDIO/clip_a_renamed.mp4")
        self.assertIsNone(after["deleted_at"])

        hist = self.conn.execute(
            "SELECT rel_path FROM asset_path_history WHERE asset_id=?", (before["id"],)).fetchall()
        paths = {h["rel_path"] for h in hist}
        self.assertIn("01_DAY1/clip_a.mp4", paths)
        self.assertIn("02_AUDIO/clip_a_renamed.mp4", paths)


class OfflineBehaviourTests(CatalogueFixture):
    def test_offline_drive_keeps_catalogue_and_search(self):
        scanner.mark_root_offline(self.conn, self.root_id)
        listing = catalogue.list_assets(self.conn, self.root_id)
        self.assertEqual(len(listing), 3, "catalogue entries must survive disconnect")

        hits = catalogue.search(self.conn, "clip")
        self.assertEqual(len(hits), 2, "search must work with the drive offline")
        for h in hits:
            state = catalogue.asset_state(self.conn, h)
            self.assertEqual(state["state"], "catalogue_only")
            self.assertFalse(state["original_available"])

    def test_browse_offline_reconstructs_folder_tree(self):
        scanner.mark_root_offline(self.conn, self.root_id)
        top = catalogue.browse(self.conn, self.root_id, "")
        self.assertEqual({f["name"] for f in top["folders"]}, {"01_DAY1", "02_AUDIO"})
        day1 = catalogue.browse(self.conn, self.root_id, "01_DAY1")
        self.assertEqual({f["name"] for f in day1["files"]}, {"clip_a.mp4", "clip_b.mp4"})
        self.assertFalse(day1["root_online"])

    def test_scan_while_offline_never_deletes(self):
        scanner.mark_root_offline(self.conn, self.root_id)
        res = scanner.scan_root(self.conn, self.root_id)
        self.assertEqual(res.status, "skipped_offline")
        self.assertEqual(res.removed, 0)
        live = self.conn.execute(
            "SELECT COUNT(*) c FROM assets WHERE deleted_at IS NULL").fetchone()["c"]
        self.assertEqual(live, 3, "an unreachable drive must never tombstone assets")

    def test_preview_only_state(self):
        scanner.mark_root_offline(self.conn, self.root_id)
        row = self.conn.execute("SELECT id FROM assets WHERE name='clip_a.mp4'").fetchone()
        preview = self.drive / "02_AUDIO" / "thumb.svg"
        preview.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        self.conn.execute(
            """INSERT INTO cached_previews(asset_id, kind, location, path, bytes, created_at, source)
               VALUES(?,?,?,?,?,?,?)""",
            (row["id"], "thumbnail", "host", str(preview), preview.stat().st_size, now_iso(), "test"))
        self.conn.commit()
        state = catalogue.asset_state(self.conn, catalogue.get_asset(self.conn, row["id"]))
        self.assertEqual(state["state"], "preview_only")
        self.assertTrue(state["preview_available"])
        self.assertFalse(state["original_available"])
        self.assertEqual(catalogue.preview_bytes_available(self.conn, row["id"]), str(preview))

    def test_reconnect_restores_original_availability(self):
        scanner.mark_root_offline(self.conn, self.root_id)
        # A paused root stays paused even though the drive is still attached:
        # owner intent is authoritative, not a filesystem probe.
        self.assertFalse(scanner.check_root_online(self.conn, self.root_id).online)
        status = scanner.resume_sharing(self.conn, self.root_id)
        self.assertTrue(status.online)
        row = self.conn.execute("SELECT * FROM assets WHERE name='clip_a.mp4'").fetchone()
        self.assertEqual(catalogue.asset_state(self.conn, row)["state"], "original_available")


class SearchTests(CatalogueFixture):
    def test_search_by_folder_and_name(self):
        self.assertEqual(len(catalogue.search(self.conn, "day1")), 2)
        self.assertEqual(len(catalogue.search(self.conn, "interview")), 1)

    def test_search_prefix(self):
        self.assertEqual(len(catalogue.search(self.conn, "cli")), 2)

    def test_search_survives_punctuation(self):
        self.assertEqual(len(catalogue.search(self.conn, "clip_a!!")), 1)
        self.assertEqual(catalogue.search(self.conn, "  "), [])

    def test_scope_search_to_root(self):
        other = self.tmp / "other_drive"
        other.mkdir()
        (other / "clip_a.mp4").write_text("different bytes")
        other_root = scanner.register_root(self.conn, str(other), "Other")
        scanner.scan_root(self.conn, other_root)
        self.assertEqual(len(catalogue.search(self.conn, "clip_a")), 2)
        self.assertEqual(len(catalogue.search(self.conn, "clip_a", root_id=other_root)), 1)


if __name__ == "__main__":
    unittest.main()
