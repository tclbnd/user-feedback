import os
import struct
import unittest

from proreskit.prores import ProresError, parse

from streams import frame, picture, stream

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, os.pardir, "examples")


def example(name):
    with open(os.path.join(EXAMPLES, name), "rb") as fh:
        return fh.read()


class BasicTests(unittest.TestCase):
    def test_single_frame(self):
        out = parse(stream(1, width=1280, height=720))
        self.assertEqual((out["width"], out["height"], out["frames"], out["creator"]), (1280, 720, 1, "apl0"))

    def test_slices(self):
        self.assertEqual(parse(stream(1, slices=12))["slices"], 12)


class FrameWalkTests(unittest.TestCase):
    def test_all_frames_are_read(self):
        # Frames sit back to back, a frame's size field covers the whole frame.
        out = parse(stream(7))
        self.assertEqual(out["frames"], 7)
        self.assertEqual((out["min_frame_bytes"], out["max_frame_bytes"]), (1659, 1659))
        self.assertFalse(out["truncated"])
        self.assertEqual(out["inconsistent_frames"], 0)

    def test_example_frames(self):
        out = parse(example("a001_c003.prores"))
        self.assertEqual((out["frames"], out["creator"]), (24, "arri"))
        self.assertFalse(out["truncated"])


class HeaderTests(unittest.TestCase):
    def test_chroma(self):
        self.assertEqual(parse(stream(1, chroma=2))["chroma"], "4:2:2")
        self.assertEqual(parse(stream(1, chroma=3))["chroma"], "4:4:4")
        self.assertIsNone(parse(stream(1, chroma=0))["chroma"])

    def test_frame_rate(self):
        expected = {0: None, 1: "24000/1001", 2: "24", 3: "25", 4: "30000/1001",
                    5: "30", 6: "50", 7: "60000/1001", 8: "60", 9: "100",
                    10: "120000/1001", 11: "120", 12: None, 13: None, 14: None, 15: None}
        for code, rate in expected.items():
            self.assertEqual(parse(stream(1, rate=code))["frame_rate"], rate, code)

    def test_scan(self):
        self.assertEqual(parse(stream(1, scan=0))["scan"], "progressive")
        self.assertEqual(parse(stream(1, scan=1))["scan"], "interlaced_tff")
        self.assertEqual(parse(stream(1, scan=2))["scan"], "interlaced_bff")

    def test_interlaced_has_two_pictures(self):
        out = parse(stream(1, scan=1, slices=8))
        self.assertEqual((out["pictures"], out["slices"]), (2, 16))
        self.assertEqual(out["inconsistent_frames"], 0)
        self.assertEqual(parse(stream(1, scan=0, slices=8))["pictures"], 1)

    def test_alpha(self):
        self.assertIsNone(parse(stream(1, alpha=0))["alpha_bits"])
        self.assertEqual(parse(stream(1, alpha=1))["alpha_bits"], 8)
        self.assertEqual(parse(stream(1, alpha=2))["alpha_bits"], 16)

    def test_color_names(self):
        names = parse(stream(1, color=(9, 16, 9)))["color_names"]
        self.assertEqual(names, {"primaries": "bt2020", "transfer": "pq", "matrix": "bt2020_ncl"})
        self.assertEqual(parse(stream(1, color=(1, 1, 1)))["color_names"],
                         {"primaries": "bt709", "transfer": "bt709", "matrix": "bt709"})
        self.assertEqual(parse(stream(1, color=(2, 2, 2)))["color_names"],
                         {"primaries": None, "transfer": None, "matrix": None})


class ExampleTests(unittest.TestCase):
    def test_arri_uhd(self):
        out = parse(example("a001_c003.prores"))
        self.assertEqual((out["width"], out["height"]), (4096, 2160))
        self.assertEqual((out["chroma"], out["scan"]), ("4:2:2", "progressive"))
        self.assertEqual(out["frame_rate"], "24000/1001")
        self.assertEqual((out["pictures"], out["slices"]), (1, 8))
        self.assertEqual(out["inconsistent_frames"], 0)

    def test_4444_interlaced(self):
        out = parse(example("graphics_4444.prores"))
        self.assertEqual((out["chroma"], out["scan"]), ("4:4:4", "interlaced_tff"))
        self.assertEqual(out["frame_rate"], "30000/1001")
        self.assertEqual((out["pictures"], out["slices"]), (2, 16))
        self.assertEqual(out["alpha_bits"], 8)
        self.assertEqual(out["inconsistent_frames"], 0)
        self.assertFalse(out["truncated"])


class ConsistencyTests(unittest.TestCase):
    def test_bad_slice_table(self):
        fr = bytearray(frame(slices=8))
        hsize = struct.unpack_from(">H", fr, 8)[0]
        p = 8 + hsize
        phsize = fr[p] >> 3
        struct.pack_into(">H", fr, p + phsize, 999)
        out = parse(bytes(fr))
        self.assertEqual(out["inconsistent_frames"], 1)

    def test_bad_picture_size(self):
        fr = bytearray(frame(slices=8))
        hsize = struct.unpack_from(">H", fr, 8)[0]
        p = 8 + hsize
        struct.pack_into(">I", fr, p + 1, 1600)
        self.assertEqual(parse(bytes(fr))["inconsistent_frames"], 1)

    def test_one_bad_frame_out_of_many(self):
        good = stream(3)
        bad = bytearray(frame())
        hsize = struct.unpack_from(">H", bad, 8)[0]
        p = 8 + hsize
        phsize = bad[p] >> 3
        struct.pack_into(">H", bad, p + phsize, 999)
        out = parse(good + bytes(bad) + good)
        self.assertEqual(out["frames"], 7)
        self.assertEqual(out["inconsistent_frames"], 1)

    def test_truncated_mid_frame(self):
        data = stream(4)
        out = parse(data[:-10])
        self.assertEqual(out["frames"], 3)
        self.assertTrue(out["truncated"])

    def test_truncated_in_size_field(self):
        data = stream(2)
        self.assertTrue(parse(data[:-1])["truncated"])
        self.assertTrue(parse(data[:-7])["truncated"])

    def test_partial_only_frame(self):
        with self.assertRaises(ProresError):
            parse(stream(1)[:-1])


class BuilderTests(unittest.TestCase):
    def test_picture_is_self_consistent(self):
        pic = picture(nslices=8)
        phsize = pic[0] >> 3
        psize, nslices = struct.unpack_from(">IH", pic, 1)
        table = struct.unpack_from(">%dH" % nslices, pic, phsize)
        self.assertEqual(phsize + 2 * nslices + sum(table), psize)
        self.assertEqual(len(pic), psize)
