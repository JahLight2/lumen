"""Range-header parsing: the contract every NLE read depends on."""

import unittest

from lfmc.net.http_range import ByteRange, RangeError, parse_range_header

SIZE = 10_000


class ParseRangeTests(unittest.TestCase):
    def test_no_header(self):
        self.assertIsNone(parse_range_header(None, SIZE))
        self.assertIsNone(parse_range_header("   ", SIZE))

    def test_closed_range(self):
        self.assertEqual(parse_range_header("bytes=0-499", SIZE), ByteRange(0, 499))
        self.assertEqual(parse_range_header("BYTES=100-199", SIZE), ByteRange(100, 199))

    def test_open_ended(self):
        self.assertEqual(parse_range_header("bytes=9900-", SIZE), ByteRange(9900, 9999))

    def test_suffix(self):
        self.assertEqual(parse_range_header("bytes=-100", SIZE), ByteRange(9900, 9999))
        # suffix larger than the file clamps to the whole file
        self.assertEqual(parse_range_header("bytes=-999999", SIZE), ByteRange(0, 9999))

    def test_end_beyond_size_clamped(self):
        self.assertEqual(parse_range_header("bytes=9990-999999", SIZE), ByteRange(9990, 9999))

    def test_length(self):
        self.assertEqual(parse_range_header("bytes=10-19", SIZE).length, 10)

    def test_unsatisfiable(self):
        with self.assertRaises(RangeError):
            parse_range_header("bytes=10000-", SIZE)

    def test_malformed(self):
        for bad in ["bytes=abc", "bytes=10-5", "bytes=", "items=0-10", "bytes=0-10,20-30"]:
            with self.assertRaises(RangeError, msg=bad):
                parse_range_header(bad, SIZE)

    def test_multi_range_rejected(self):
        # Deliberate: never silently answer with a whole file.
        with self.assertRaises(RangeError):
            parse_range_header("bytes=0-10, 20-30", SIZE)


if __name__ == "__main__":
    unittest.main()
