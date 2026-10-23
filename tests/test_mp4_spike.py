"""Parser correctness for the ISO-BMFF ranged-read spike.

Uses synthetic files with a known layout, so these assertions test the parser,
not any camera codec. Real-codec coverage requires real footage (see RISKS.md).
"""

import tempfile
import unittest
from pathlib import Path

from lfmc.spikes.mp4_remote_open import (
    FileRangeReader, analyse_source, analyze, build_box, read_box_tree,
    write_synthetic_mp4,
)


class BoxParserTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_nested_containers_are_walked(self):
        path = self.dir / "nested.mp4"
        path.write_bytes(
            build_box("ftyp", b"isom" + b"\x00" * 8)
            + build_box("moov", build_box("trak", build_box("mdia", b"\x00" * 32)))
            + build_box("mdat", b"\x00" * 128)
        )
        reader = FileRangeReader(str(path))
        try:
            boxes = read_box_tree(reader)
        finally:
            reader.close()
        types = [b.type for b in boxes]
        self.assertEqual(types[0], "ftyp")
        self.assertIn("moov", types)
        self.assertIn("trak", types)
        self.assertIn("mdia", types)
        self.assertIn("mdat", types)

    def test_64bit_box_size(self):
        payload = b"\x00" * 16
        big = b"\x00\x00\x00\x01" + b"mdat" + (24).to_bytes(8, "big") + payload
        path = self.dir / "large.mp4"
        path.write_bytes(big)
        reader = FileRangeReader(str(path))
        try:
            boxes = read_box_tree(reader)
        finally:
            reader.close()
        self.assertEqual(boxes[0].type, "mdat")
        self.assertEqual(boxes[0].header_size, 16)
        self.assertEqual(boxes[0].size, 24)


class RangedOpenCostTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_moov_at_end_detected_and_cost_is_small(self):
        info = write_synthetic_mp4(str(self.dir / "tail.mp4"), media_bytes=16 << 20,
                                   moov_at_end=True)
        reader = FileRangeReader(info["path"])
        try:
            result = analyze(reader, seeks=10, chunk_bytes=2 << 20)
        finally:
            reader.close()
        self.assertIsNotNone(result.moov_offset)
        self.assertGreater(result.moov_offset, 1_000_000, "moov really is at the end")
        self.assertFalse(result.moov_before_mdat)
        self.assertLess(result.bytes_fetched_for_open, 64 * 1024,
                        "reading structure must be a few KB, not megabytes")
        self.assertLess(result.open_bytes_ratio, 0.01)

    def test_moov_at_start_detected(self):
        info = write_synthetic_mp4(str(self.dir / "head.mp4"), media_bytes=4 << 20,
                                   moov_at_end=False)
        reader = FileRangeReader(info["path"])
        try:
            result = analyze(reader)
        finally:
            reader.close()
        self.assertTrue(result.moov_before_mdat)

    def test_seek_model_is_labelled_as_a_model(self):
        info = write_synthetic_mp4(str(self.dir / "model.mp4"), media_bytes=8 << 20)
        report = analyse_source(info["path"], seeks=10, chunk_bytes=2 << 20)
        self.assertIn("MODELLED", report["model_notes"])
        self.assertEqual(report["modelled_seek_bytes"], 20 << 20)
        self.assertGreater(report["modelled_total_ratio"], report["open_bytes_ratio"])


if __name__ == "__main__":
    unittest.main()
