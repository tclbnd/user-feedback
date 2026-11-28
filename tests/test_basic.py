import struct
import unittest
import zlib

from pickit.pictor import EGA, PicError, parse, rgb


EMPTY_EXT = b"\xFF\x00" + struct.pack("<HH", 0, 0)


def header(w, h, bpp_plane, planes):
    t = bpp_plane | ((planes - 1) << 4)
    # magic, width, height, x offset, y offset, depth byte = 11 bytes
    return struct.pack("<HHHHHB", 0x1234, w, h, 0, 0, t)


def ega_ext(entries):
    return b"\xFF\x00" + struct.pack("<HH", 3, len(entries)) + bytes(entries)


def vga_ext(channels):
    body = bytes(channels)
    return b"\xFF\x00" + struct.pack("<HH", 4, len(body)) + body


def rle_block(marker, runs):
    body = bytes([marker])
    for value, run in runs:
        if run == 1 and value != marker:
            body += bytes([value])
        else:
            body += bytes([marker, run & 0xFF, value])
    return struct.pack("<HH", len(body) + 4, 0) + body


def build(w, h, bpp_plane, planes, ext, encoded, payload):
    return (header(w, h, bpp_plane, planes) + ext
            + struct.pack("<H", encoded) + payload)


class BasicTests(unittest.TestCase):
    def test_raw_picture(self):
        head = (0x1234).to_bytes(2, "little") + (4).to_bytes(2, "little") + (2).to_bytes(2, "little") + bytes(4) + bytes([0x08])
        ext = b"\xFF\x00" + (0).to_bytes(2, "little") + (0).to_bytes(2, "little")
        out = parse(head + ext + bytes(2) + bytes(8))
        self.assertEqual((out["width"], out["height"]), (4, 2))

    def test_bad_magic(self):
        with self.assertRaises(PicError):
            parse(bytes(16))

    def test_rows_are_bottom_to_top(self):
        # 4x2, 8bpp, raw: the first bytes are the bottom row. A custom VGA
        # palette maps index v to (4v, 0, 0) (six-bit v scaled to eight) so
        # the row order is easy to check.
        channels = []
        for i in range(24):
            channels += [i, 0, 0]
        ext = vga_ext(channels)
        bottom, top = [10, 11, 12, 13], [20, 21, 22, 23]
        pic = build(4, 2, 8, 1, ext, 0, bytes(bottom + top))
        out = parse(pic)
        top_rgb = b"".join(bytes([4 * v, 0, 0]) for v in top)
        full_rgb = b"".join(bytes([4 * v, 0, 0]) for v in top + bottom)
        self.assertEqual(out["first_row"], "%08x" % zlib.crc32(top_rgb))
        self.assertEqual(out["crc32"], "%08x" % zlib.crc32(full_rgb))

    def test_run_exactly_filling_row_does_not_skip_one(self):
        # 4x2 8bpp RLE: bottom row four 7s, top row four 9s. The 4-long run
        # reaches exactly the row end; no extra blank row may be consumed.
        payload = rle_block(0xFE, [(7, 4), (9, 4)])
        pic = build(4, 2, 8, 1, EMPTY_EXT, 1, payload)
        out = parse(pic)
        self.assertEqual(out["colours"], 2)
        top_rgb = b"".join(bytes(rgb(EGA[9])) for _ in range(4))
        self.assertEqual(out["first_row"], "%08x" % zlib.crc32(top_rgb))

    def test_long_run_256_wraps_rows_without_gap(self):
        # A run of 256 (encoded as marker, 0, value) must cover four full
        # 64-pixel rows of a 64x4 picture, leaving no unfilled background.
        payload = rle_block(0xFE, [(5, 256)])
        pic = build(64, 4, 8, 1, EMPTY_EXT, 1, payload)
        out = parse(pic)
        self.assertEqual(out["colours"], 1)

    def test_four_planes_combine_into_pixel_index(self):
        # 8x2, four 1bpp planes, raw: set column 3 of every row to index
        # 0b1010 (10); every other pixel stays index 0.
        plane_bytes = []
        for plane in range(4):
            row_byte = ((10 >> plane) & 1) << (7 - 3)
            plane_bytes += [row_byte, row_byte]  # bottom row, top row
        pic = build(8, 2, 1, 4, EMPTY_EXT, 0, bytes(plane_bytes))
        out = parse(pic)
        self.assertEqual((out["planes"], out["bits"]), (4, 4))
        self.assertEqual(out["colours"], 2)
        black, colour = (0, 0, 0), rgb(EGA[10])
        row = [colour if x == 3 else black for x in range(8)]
        row_rgb = b"".join(bytes(c) for c in row)
        self.assertEqual(out["first_row"], "%08x" % zlib.crc32(row_rgb))

    def test_ega_palette_extension_uses_64_colour_table(self):
        # EGA entry 6 -> EGA colour 24, dark cyan 0x005555 (not CGA brown).
        entries = [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60]
        payload = rle_block(0xFE, [(6, 1)])
        pic = build(1, 1, 8, 1, ega_ext(entries), 1, payload)
        out = parse(pic)
        self.assertEqual(out["crc32"],
                         "%08x" % zlib.crc32(bytes(rgb(EGA[24]))))

    def test_vga_palette_six_bits_are_scaled(self):
        # A six-bit 63 must be scaled to 252 when shown on the PC.
        payload = rle_block(0xFE, [(0, 1)])
        pic = build(1, 1, 8, 1, vga_ext([63, 0, 0]), 1, payload)
        out = parse(pic)
        self.assertEqual(out["crc32"],
                         "%08x" % zlib.crc32(bytes([252, 0, 0])))


if __name__ == "__main__":
    unittest.main()
