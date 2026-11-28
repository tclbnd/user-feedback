import struct
import unittest
import zlib

from dsvkit.dsv import DsvError, lzss, parse, rle


def pack(width, height, frames):
    """Build a DSVPACK1 file.  Each frame is (ptype, palette, ftype, bitmap)."""
    out = [b"DSVPACK1", struct.pack("<HHH", width, height, len(frames))]
    for ptype, palette, ftype, bitmap in frames:
        pkt = bytes((ptype,)) + struct.pack("<H", len(palette)) + bytes((ftype,))
        for colour in palette:
            pkt += bytes((colour >> 16 & 0xFF, colour >> 8 & 0xFF, colour & 0xFF))
        pkt += bitmap
        out += [struct.pack("<I", len(pkt)), pkt]
    return b"".join(out)


def rgb(indices, palette, width, height, flip=True):
    rows = range(height - 1, -1, -1) if flip else range(height)
    return b"".join(bytes((palette[v] >> 16 & 0xFF, palette[v] >> 8 & 0xFF, palette[v] & 0xFF))
                    for y in rows for v in indices[y * width:(y + 1) * width])


class BitmapTests(unittest.TestCase):
    def test_rle_run_is_length_plus_one(self):
        # 0x81: high bit set, run of (0x81 & 0x7F) + 1 = 2 pixels.
        self.assertEqual(rle(b"\x81\x05", 2), b"\x05\x05")
        self.assertEqual(rle(b"\x82\x05", 3), b"\x05\x05\x05")

    def test_lzss_flags_are_lsb_first(self):
        # flag 0x01: the first (least significant) action is a literal, so the
        # byte after the flag is copied; the next action is a back reference.
        # Reading the flag most significant first would start with a reference.
        self.assertEqual(lzss(b"\x01\x07\x00\x00", 3), b"\x07\x07\x07")

    def test_lzss_reference_distance_is_field_plus_one(self):
        # flag 0x01: literal 7, then cmd 0x0003: distance (0 >> 4) + 1 = 1,
        # length (3 & 15) + 2 = 5.
        self.assertEqual(lzss(b"\x01\x07\x03\x00", 6), b"\x07" * 6)


class FrameTests(unittest.TestCase):
    def test_rows_are_stored_bottom_up(self):
        palette = [0x000000, 0x112233, 0x445566]
        # Stored order is 1, 1, 2, 2 -> bottom row is colour 1, top row colour 2.
        data = pack(2, 2, [(0, palette, 9, b"\x03\x01\x01\x02\x02")])
        out = parse(data)
        expected = rgb([2, 2, 1, 1], palette, 2, 2, flip=False)
        self.assertEqual(out["frames"][0]["crc32"], "%08x" % zlib.crc32(expected))
        self.assertEqual(expected[:3], b"\x44\x55\x66")

    def test_delta_frame_adds_to_previous(self):
        palette = [0x000000, 0x010203, 0x020406]
        # The palette holds colours for both frames; only the bitmap differs.
        data = pack(2, 1, [
            (0, palette, 9, b"\x01\x01\x01"),
            (0, [], 34, b"\x00\x01"),  # only the first pixel changes, by +1
        ])
        out = parse(data)
        first = rgb([1, 1], palette, 2, 1, flip=False)
        second = rgb([2, 1], palette, 2, 1, flip=False)
        self.assertEqual([f["crc32"] for f in out["frames"]],
                         ["%08x" % zlib.crc32(first), "%08x" % zlib.crc32(second)])

    def test_palette_survives_frames_without_one(self):
        palette = [0x000000, 0x0A0B0C]
        data = pack(1, 1, [
            (0, palette, 9, b"\x00\x01"),
            (0, [], 9, b"\x00\x01"),
        ])
        out = parse(data)
        self.assertEqual(out["frames"][0]["crc32"], out["frames"][1]["crc32"])


class HuffmanTests(unittest.TestCase):
    @staticmethod
    def table(values):
        t = bytearray(15)
        for code, value in values.items():
            t[code] = value
        return bytes(t)

    def test_huffman_rle_repeats_one_extra_pixel(self):
        # Decoded stream must be 0x81 0x01: a run of (0x81 & 0x7F) + 1 = 2 ones.
        # Nibbles: 8 -> 0x81, 1 -> 0x01.
        table = self.table({8: 0x81, 1: 0x01})
        palette = [0x000000, 0xFFFFFF]
        data = pack(2, 1, [(0, palette, 35, table + b"\x81")])
        decoded = parse(data)["frames"][0]
        expected = rgb([1, 1], palette, 2, 1, flip=False)
        self.assertEqual(decoded, {"state": "decoded", "crc32": "%08x" % zlib.crc32(expected)})

    def test_plain_huffman_keeps_rest_of_previous_frame(self):
        # 1x4 picture; the packet only supplies the first two pixels (1, 2).
        palette = [0x000000, 0x010203, 0x040506]
        base = b"\x03\x01\x01\x01\x01"  # RLE: four 1s
        table = self.table({0: 1, 1: 2})
        data = pack(1, 4, [
            (0, palette, 9, base),
            (0, [], 37, table + b"\x01"),
        ])
        out = parse(data)
        expected = rgb([1, 2, 1, 1], palette, 1, 4)
        self.assertEqual(out["frames"][1]["crc32"], "%08x" % zlib.crc32(expected))
        # The packet never mentions the last two pixels, so without the previous
        # frame they would come out black instead of 1.
        stale = rgb([1, 2, 0, 0], palette, 1, 4)
        self.assertNotEqual(out["frames"][1]["crc32"], "%08x" % zlib.crc32(stale))


class BasicTests(unittest.TestCase):
    def test_example_header(self):
        with open("examples/intro.dsv", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (48, 20, 4))

    def test_bad_magic(self):
        with self.assertRaises(DsvError):
            parse(bytes(32))


if __name__ == "__main__":
    unittest.main()
