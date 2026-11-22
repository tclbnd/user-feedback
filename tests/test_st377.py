import unittest
from fractions import Fraction

from mxfkit.mxf import parse, MxfError

from files import (H, klv, partition, std_partition, primer, dynamic_primer,
                   header_metadata, index_segment, element, rip, mxf)


class RateDurationTests(unittest.TestCase):
    def test_fractional_edit_rate_2997(self):
        out = parse(mxf(header_metadata(Fraction(30000, 1001), 2997), frames=2))
        self.assertEqual(out["edit_rate"], "30000/1001")
        self.assertEqual(out["duration_edit_units"], 2997)
        self.assertEqual(out["duration"], str(Fraction(2997) * Fraction(1001, 30000)))

    def test_integer_rate_renders_without_denominator(self):
        out = parse(mxf(header_metadata(25, 100), frames=1))
        self.assertEqual(out["edit_rate"], "25")
        self.assertEqual(out["duration"], "4")

    def test_open_duration_is_null(self):
        out = parse(mxf(header_metadata(25, 100, open_duration=True), frames=1))
        self.assertIsNone(out["duration_edit_units"])
        self.assertIsNone(out["duration"])


class VideoDescriptorTests(unittest.TestCase):
    def test_interlaced_field_height_doubled(self):
        for layout in (1, 4):
            out = parse(mxf(header_metadata(25, 50, (1920, 540, layout, 10)), frames=1))
            v = out["video"][0]
            self.assertEqual((v["width"], v["height"]), (1920, 1080))
            self.assertTrue(v["interlaced"])

    def test_progressive_height_unchanged(self):
        out = parse(mxf(header_metadata(50, 50, (1280, 720, 0, 8)), frames=1))
        v = out["video"][0]
        self.assertEqual((v["width"], v["height"], v["interlaced"]), (1280, 720, False))

    def test_rgba_descriptor(self):
        blob = mxf(header_metadata(24, 10, (640, 480, 0, 8), audio=()), frames=1)
        blob = blob.replace(H("060e2b34025301010d01010101280000"),
                            H("060e2b34025301010d01010101290000"))
        v = parse(blob)["video"][0]
        self.assertEqual((v["width"], v["height"]), (640, 480))


class AudioDescriptorTests(unittest.TestCase):
    def test_sample_rate_is_rational(self):
        out = parse(mxf(header_metadata(25, 100), frames=1))
        self.assertEqual(out["audio"][0]["sample_rate"], "48000")
        self.assertEqual(out["audio"][0]["channels"], 2)
        self.assertEqual(out["audio"][0]["bits"], 24)


class EssenceTests(unittest.TestCase):
    def _file_with_items(self, items):
        blob = (partition("header", 1) + primer()
                + b"".join(header_metadata(25, 10, video=None, audio=())))
        for item in items:
            blob += klv(H("060e2b34010201010d010301") + bytes([item, 1, 1, 1]), b"x")
        return blob + partition("footer", 4, 0, 1) + index_segment() + rip()

    def test_all_item_types_and_unknown(self):
        out = parse(self._file_with_items(
            [0x04, 0x14, 0x05, 0x15, 0x06, 0x16, 0x07, 0x17, 0x08, 0x18, 0x1f]))
        self.assertEqual(out["essence"],
                         {"system": 2, "picture": 2, "sound": 2, "data": 2,
                          "compound": 2, "other": 1})


class IndexTests(unittest.TestCase):
    def test_duplicate_segment_counts_once(self):
        seg = index_segment(start=0, duration=100)
        out = parse(mxf(header_metadata(25, 100), frames=1, index=[seg, seg]))
        self.assertEqual(out["index"]["segments"], 1)
        self.assertEqual(out["index"]["edit_units"], 100)

    def test_distinct_segments_sum(self):
        out = parse(mxf(header_metadata(25, 150), frames=1,
                        index=[index_segment(0, 100), index_segment(100, 50)]))
        self.assertEqual(out["index"]["segments"], 2)
        self.assertEqual(out["index"]["edit_units"], 150)

    def test_constant_bytes(self):
        out = parse(mxf(header_metadata(25, 10), frames=1,
                        index=[index_segment(duration=10, cbe=4096)]))
        self.assertEqual(out["index"]["constant_bytes"], 4096)
        out = parse(mxf(header_metadata(25, 10), frames=1,
                        index=[index_segment(duration=5, cbe=100),
                               index_segment(start=5, duration=5, cbe=200)]))
        self.assertIsNone(out["index"]["constant_bytes"])


class PartitionTests(unittest.TestCase):
    def test_run_in(self):
        out = parse(b"\x00" * 16 + mxf(header_metadata(25, 100), frames=1))
        self.assertEqual(out["run_in"], 16)

    def test_closed_complete_copy_preferred(self):
        blob = (partition("header", 1) + primer()
                + b"".join(header_metadata(25, 100, (640, 480, 0, 8))))
        blob += (partition("footer", 4, 0, 1) + primer()
                 + b"".join(header_metadata(25, 200, (1920, 1080, 0, 10)))
                 + index_segment(duration=200) + rip())
        out = parse(blob)
        self.assertEqual(out["duration_edit_units"], 200)
        self.assertEqual(out["video"][0]["width"], 1920)

    def test_fallback_when_no_closed_complete_copy(self):
        blob = (partition("header", 1) + primer()
                + b"".join(header_metadata(25, 100))
                + partition("body", 1, 1, 0) + partition("footer", 1, 0, 1) + rip())
        out = parse(blob)
        self.assertEqual(out["duration_edit_units"], 100)
        self.assertTrue(out["video"])

    def test_op_atom(self):
        blob = mxf(header_metadata(25, 100))
        blob = blob.replace(partition("header", 4), partition("header", 4, op="OP-Atom"), 1)
        self.assertEqual(parse(blob)["operational_pattern"], "OP-Atom")

    def test_standard_st377_partition_layout(self):
        blob = std_partition("header", 1) + primer() + b"".join(header_metadata(25, 100))
        blob += element("picture", 1, 10) + element("sound", 1, 8)
        blob += std_partition("body", 2, body_sid=1, index_sid=7)
        blob += (std_partition("footer", 3, index_sid=7)
                 + index_segment(duration=100, index_sid=7, body_sid=1) + rip())
        out = parse(blob)
        self.assertEqual(
            [(p["kind"], p["closed"], p["complete"], p["body_sid"], p["index_sid"])
             for p in out["partitions"]],
            [("header", False, False, 0, 0), ("body", True, False, 1, 7),
             ("footer", False, True, 0, 7)])
        self.assertEqual(out["essence"]["sound"], 1)
        self.assertEqual(out["index"]["edit_units"], 100)

    def test_standard_layout_op_atom_with_run_in(self):
        blob = b"\xaa" * 8 + std_partition("header", 0, op="OP-Atom") + primer()
        blob += b"".join(header_metadata(25, 50)) + element("picture", 1, 10)
        blob += std_partition("footer", 3, op="OP-Atom") + rip()
        out = parse(blob)
        self.assertEqual(out["run_in"], 8)
        self.assertEqual(out["operational_pattern"], "OP-Atom")


class PrimerTests(unittest.TestCase):
    def test_dynamic_local_tags(self):
        tagmap = {0x4B01: 0x7F01, 0x3D03: 0x7F02, 0x3202: 0x7F03}
        sets = header_metadata(25, 100, (1280, 720, 0, 8), tagmap=tagmap)
        blob = (partition("header", 4) + dynamic_primer(tagmap) + b"".join(sets)
                + element("picture", 1, 10) + element("sound", 1, 8)
                + partition("footer", 4, 0, 1) + index_segment(duration=100) + rip())
        out = parse(blob)
        self.assertEqual(out["edit_rate"], "25")
        self.assertEqual(out["video"][0]["height"], 720)
        self.assertEqual(out["audio"][0]["sample_rate"], "48000")


class ErrorTests(unittest.TestCase):
    def test_missing_header_raises(self):
        with self.assertRaises(MxfError):
            parse(b"not an mxf file at all")


if __name__ == "__main__":
    unittest.main()
