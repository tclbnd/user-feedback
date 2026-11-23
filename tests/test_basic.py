import unittest
from fractions import Fraction

from mxfkit.mxf import parse

from files import header_metadata, index_segment, mxf

NTSC = Fraction(30000, 1001)


class BasicTests(unittest.TestCase):
    def test_partitions(self):
        out = parse(mxf(header_metadata(25, 100), frames=3))
        self.assertEqual([p["kind"] for p in out["partitions"]], ["header", "footer"])
        self.assertEqual((out["operational_pattern"], out["rip"]), ("OP1a", True))

    def test_video(self):
        v = parse(mxf(header_metadata(25, 100, (1280, 720, 0, 8)), frames=2))["video"][0]
        self.assertEqual((v["width"], v["height"], v["bit_depth"]), (1280, 720, 8))


class RateTests(unittest.TestCase):
    def test_fractional_rate_is_kept(self):
        out = parse(mxf(header_metadata(NTSC, 1798, (1920, 540, 1, 10))))
        self.assertEqual(out["edit_rate"], "30000/1001")
        self.assertEqual(out["duration_edit_units"], 1798)
        self.assertEqual(Fraction(out["duration"]), Fraction(1798) / NTSC)

    def test_integer_rate(self):
        out = parse(mxf(header_metadata(50, 1500, (1280, 720, 0, 8))))
        self.assertEqual((out["edit_rate"], out["duration"]), ("50", "30"))

    def test_source_package_duration_is_not_used(self):
        out = parse(mxf(header_metadata(25, 250, source_duration=999)))
        self.assertEqual(out["duration_edit_units"], 250)

    def test_unknown_duration_falls_back_to_container(self):
        out = parse(mxf(header_metadata(NTSC, 1798, (1920, 540, 1, 10), open_duration=True)))
        self.assertEqual(out["duration_edit_units"], 1798)


class VideoTests(unittest.TestCase):
    def test_separate_fields_double_the_stored_height(self):
        v = parse(mxf(header_metadata(NTSC, 100, (1920, 540, 1, 10))))["video"][0]
        self.assertEqual((v["height"], v["interlaced"]), (1080, True))

    def test_progressive_height_is_not_doubled(self):
        v = parse(mxf(header_metadata(25, 100, (1920, 1080, 0, 10))))["video"][0]
        self.assertEqual((v["height"], v["interlaced"]), (1080, False))


class AudioTests(unittest.TestCase):
    def test_sample_rate_is_a_rational(self):
        a = parse(mxf(header_metadata(25, 100, audio=((48000, 2, 24), (44100, 1, 16)))))["audio"]
        self.assertEqual([x["sample_rate"] for x in a], ["48000", "44100"])
        self.assertEqual([x["bits"] for x in a], [24, 16])


class EssenceTests(unittest.TestCase):
    def test_elements_are_counted_by_kind(self):
        out = parse(mxf(header_metadata(25, 100, audio=((48000, 2, 24),)), frames=4))
        self.assertEqual(out["essence"], {"picture": 4, "sound": 4, "system": 4})


class IndexTests(unittest.TestCase):
    def test_distinct_segments_are_counted_once(self):
        seg = [index_segment(0, 250)]
        self.assertEqual(parse(mxf(header_metadata(25, 250), index=seg * 3))["index"]["segments"], 1)

    def test_segments_cover_their_edit_units(self):
        seg = [index_segment(0, 100), index_segment(100, 150)]
        out = parse(mxf(header_metadata(25, 250), index=seg))["index"]
        self.assertEqual((out["segments"], out["edit_units"]), (2, 250))

    def test_constant_byte_count(self):
        out = parse(mxf(header_metadata(25, 250), index=[index_segment(0, 250, cbe=200)]))["index"]
        self.assertEqual(out["constant_bytes"], 200)


class MetadataCopyTests(unittest.TestCase):
    def test_closed_complete_copy_wins(self):
        good = header_metadata(25, 250, (1920, 1080, 0, 10))
        stale = header_metadata(30, 60, (1280, 720, 0, 8))
        for header_status in (1, 2):        # open, then closed but incomplete
            out = parse(mxf(stale, header_status=header_status, footer_sets=good))
            self.assertEqual(out["duration_edit_units"], 250)

    def test_run_in_is_measured(self):
        out = parse(mxf(header_metadata(25, 100), run_in=b"\0" * 64))
        self.assertEqual(out["run_in"], 64)


class OperationalPatternTests(unittest.TestCase):
    def test_op_atom(self):
        self.assertEqual(parse(mxf(header_metadata(25, 100), op="OP-Atom"))["operational_pattern"], "OP-Atom")
