import struct
import unittest
import zlib

from mwsckit.mwsc import Mwsc, MwscError, parse, _to_rgb_top_down


def rec(b, g, r, run):
    return bytes((b, g, r, run))


def fill_long(b, g, r, count):
    # Long fill: pixel + run byte 0 + u32 little-endian count.
    return rec(b, g, r, 0) + struct.pack("<I", count)


def copy_rec(count):
    # Copy: u24 little-endian count in the pixel bytes, run byte 255.
    return rec(count & 0xff, (count >> 8) & 0xff, (count >> 16) & 0xff, 255)


def pack(w, h, streams):
    out = b"MWSCPACK" + struct.pack("<HHH", w, h, len(streams))
    for s in streams:
        pkt = zlib.compress(s)
        out += struct.pack("<I", len(pkt)) + pkt
    return out


class BasicTests(unittest.TestCase):
    def test_example_header(self):
        with open("examples/lesson.mwsc", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (64, 40, 3))

    def test_bad_magic(self):
        with self.assertRaises(MwscError):
            parse(bytes(16))

    def test_example_all_frames_decode_with_key_flags(self):
        with open("examples/lesson.mwsc", "rb") as fh:
            out = parse(fh.read())
        states = [(f["state"], f["key"]) for f in out["frames"]]
        self.assertEqual(states,
                         [("decoded", True), ("decoded", False), ("decoded", False)])

    def test_long_run_beyond_254_keeps_aligned(self):
        # A 2x2 picture = 4 pixels. A run longer than 254 must use the u32
        # form and paint every pixel instead of stopping at 256 and skewing
        # the rest of the stream.
        w, h = 2, 2
        stream = fill_long(1, 2, 3, 4)
        cur, intra = Mwsc(w, h).frame(zlib.compress(stream))
        self.assertTrue(intra)
        self.assertEqual(cur, bytes((1, 2, 3)) * 4)

    def test_normal_runs_still_work(self):
        w, h = 2, 2
        stream = rec(10, 11, 12, 3) + rec(4, 5, 6, 1)
        cur, _ = Mwsc(w, h).frame(zlib.compress(stream))
        self.assertEqual(cur, bytes((10, 11, 12)) * 3 + bytes((4, 5, 6)))

    def test_copy_brings_pixels_from_previous_frame(self):
        w, h = 1, 3
        base = Mwsc(w, h)
        base.frame(zlib.compress(rec(7, 8, 9, 3)))          # key frame
        # Keep the first pixel, repaint the other two, then no more data.
        cur, intra = base.frame(zlib.compress(copy_rec(1) + rec(1, 1, 1, 2)))
        self.assertFalse(intra)
        self.assertEqual(cur, bytes((7, 8, 9)) + bytes((1, 1, 1)) * 2)

    def test_copy_record_is_not_an_error(self):
        # A whole frame expressed as one copy must decode (it previously
        # raised "copy records are not supported").
        w, h = 4, 4
        base = Mwsc(w, h)
        base.frame(zlib.compress(rec(2, 3, 4, 16)))
        cur, intra = base.frame(zlib.compress(copy_rec(16)))
        self.assertFalse(intra)
        self.assertEqual(cur, bytes((2, 3, 4)) * 16)

    def test_output_is_top_down_rgb(self):
        # Native buffer is bottom-up BGR. Bottom row = red (B=0,G=0,R=255),
        # top row = blue (B=255,G=0,R=0). The top-down RGB output must start
        # with the blue row as RGB (0, 0, 255) and end with red (255, 0, 0).
        w, h = 1, 2
        stream = rec(0, 0, 255, 1) + rec(255, 0, 0, 1)     # bottom row first
        cur, _ = Mwsc(w, h).frame(zlib.compress(stream))
        rgb = _to_rgb_top_down(cur, w, h, w * 3)
        self.assertEqual(rgb, bytes((0, 0, 255, 255, 0, 0)))

    def test_parse_crc_uses_flipped_swapped_picture(self):
        w, h = 1, 2
        stream = rec(0, 0, 255, 1) + rec(255, 0, 0, 1)
        out = parse(pack(w, h, [stream]))
        frame = out["frames"][0]
        self.assertEqual(frame["state"], "decoded")
        expect = zlib.crc32(bytes((0, 0, 255, 255, 0, 0)))
        self.assertEqual(frame["crc32"], "%08x" % expect)

    def test_short_picture_is_rejected(self):
        # Records cover fewer pixels than the frame: the codec rejects it.
        with self.assertRaises(MwscError):
            Mwsc(2, 2).frame(zlib.compress(rec(1, 2, 3, 3)))

    def test_overrun_picture_is_rejected(self):
        with self.assertRaises(MwscError):
            Mwsc(2, 2).frame(zlib.compress(fill_long(1, 2, 3, 5)))

    def test_truncated_long_count_is_rejected(self):
        with self.assertRaises(MwscError):
            Mwsc(2, 2).frame(zlib.compress(rec(1, 2, 3, 0) + b"\x02\x00"))


if __name__ == "__main__":
    unittest.main()
