"""RDD 36 bit-field, multi-frame, interlace and consistency regressions."""
import struct
import unittest

from proreskit.prores import ProresError, parse

from streams import frame, stream


class FrameHeaderTests(unittest.TestCase):
    def test_chroma_codes(self):
        self.assertEqual(parse(stream(1, chroma=2))["chroma"], "4:2:2")
        self.assertEqual(parse(stream(1, chroma=3))["chroma"], "4:4:4")

    def test_scan_modes_and_pictures(self):
        for scan, label, pictures in ((0, "progressive", 1),
                                      (1, "interlaced_tff", 2),
                                      (2, "interlaced_bff", 2)):
            out = parse(stream(1, scan=scan))
            self.assertEqual(out["scan"], label)
            self.assertEqual(out["pictures"], pictures)

    def test_rate_codes(self):
        for code, label in ((1, "23.976"), (2, "24"), (3, "25"), (4, "29.97"),
                            (5, "30"), (6, "50"), (7, "59.94"), (8, "60"), (0, None)):
            self.assertEqual(parse(stream(1, rate=code))["frame_rate"], label)

    def test_alpha_codes(self):
        self.assertIsNone(parse(stream(1, alpha=0))["alpha_bits"])
        self.assertEqual(parse(stream(1, alpha=1))["alpha_bits"], 8)
        self.assertEqual(parse(stream(1, alpha=2))["alpha_bits"], 16)

    def test_aspect_codes(self):
        for code, label in ((0, None), (1, "1:1"), (2, "4:3"), (3, "16:9")):
            self.assertEqual(parse(stream(1, aspect=code))["aspect"], label)


class StreamTests(unittest.TestCase):
    def test_all_frames_found(self):
        out = parse(stream(24))
        self.assertEqual(out["frames"], 24)
        self.assertFalse(out["truncated"])

    def test_interlaced_slices_counted_per_field(self):
        out = parse(stream(3, scan=1, slices=6))
        self.assertEqual(out["pictures"], 2)
        self.assertEqual(out["slices"], 12)
        self.assertEqual(out["frames"], 3)
        self.assertEqual(out["inconsistent_frames"], 0)

    def test_frame_size_stats(self):
        out = parse(stream(5, slices=4))
        size = struct.unpack(">I", stream(1, slices=4)[:4])[0]
        self.assertEqual((out["min_frame_bytes"], out["max_frame_bytes"]), (size, size))

    def test_truncated_in_middle_of_frame(self):
        full = stream(4)
        size = struct.unpack(">I", full[:4])[0]
        out = parse(full[:2 * size + 10])
        self.assertEqual(out["frames"], 2)
        self.assertTrue(out["truncated"])

    def test_truncated_with_only_header_words(self):
        full = stream(2)
        size = struct.unpack(">I", full[:4])[0]
        out = parse(full[:size + 11])
        self.assertEqual(out["frames"], 1)
        self.assertTrue(out["truncated"])

    def test_trailing_bytes_after_frame(self):
        out = parse(stream(2) + b"\x00\x00\x00")
        self.assertEqual(out["frames"], 2)
        self.assertTrue(out["truncated"])

    def test_no_frame(self):
        with self.assertRaises(ProresError):
            parse(b"\x00" * 16)

    def test_bad_magic(self):
        with self.assertRaises(ProresError):
            parse(struct.pack(">I", 8) + b"xxxx")


def corrupt_frame_slice_table(index=0):
    data = stream(3)
    size = struct.unpack(">I", data[:4])[0]
    hsize = struct.unpack(">H", data[8:10])[0]
    p = index * size + 8 + hsize
    # overwrite the first slice size in the table with an impossible value
    table_pos = p + (data[p] >> 3)
    data = data[:table_pos] + struct.pack(">H", 9999) + data[table_pos + 2:]
    return data


def corrupt_frame_picture_size():
    data = stream(3)
    size = struct.unpack(">I", data[:4])[0]
    hsize = struct.unpack(">H", data[8:10])[0]
    p = size + 8 + hsize  # second frame, its picture
    data = data[:p + 1] + struct.pack(">I", 10) + data[p + 5:]
    return data


class ConsistencyTests(unittest.TestCase):
    def test_bad_slice_table_flagged(self):
        out = parse(corrupt_frame_slice_table())
        self.assertEqual(out["frames"], 3)
        self.assertEqual(out["inconsistent_frames"], 1)

    def test_bad_picture_size_flagged(self):
        out = parse(corrupt_frame_picture_size())
        self.assertEqual(out["frames"], 3)
        self.assertEqual(out["inconsistent_frames"], 1)

    def test_progressive_frame_with_two_pictures_is_inconsistent(self):
        # scan=0 means one picture per spec; a frame whose pictures overrun
        # the container must be reported as inconsistent.
        one = frame()
        size = struct.unpack(">I", one[:4])[0]
        hsize = struct.unpack(">H", one[8:10])[0]
        pic = one[8 + hsize:size]
        body = b"icpf" + one[8:8 + hsize] + pic + pic
        bad = struct.pack(">I", len(body) + 4) + body
        out = parse(bad + frame())
        self.assertEqual(out["inconsistent_frames"], 1)
        self.assertEqual(out["frames"], 2)


if __name__ == "__main__":
    unittest.main()
