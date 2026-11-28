import struct
import unittest
import zlib

from icokit.ico import IcoError, parse


def icon_8bit(w, h):
    hdr = struct.pack("<IiiHHIIiiII", 40, w, 2 * h, 1, 8, 0, 0, 0, 0, 0, 0)
    img = hdr + bytes(1024) + bytes(w * h) + bytes(((w + 31) // 32) * 4 * h)
    return struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", w, h, 0, 0, 1, 8, len(img), 22) + img


def build(kind, bpp, w, h, pixels, mask_rows=None, palette=None, hotspot=(0, 0)):
    """Assemble a one-image icon/cursor file.

    `pixels` is a list of rows, top-down, each a list of palette indices (bpp
    <= 8) or (r, g, b) / (r, g, b, a) tuples. `mask_rows` is top-down too, one
    0/1 per pixel. The DIB stores everything bottom-up, which this helper does.
    """
    palette = palette or [(0, 0, 0), (255, 255, 255)]
    hdr = struct.pack("<IiiHHIIiiII", 40, w, 2 * h, 1, bpp, 0, 0, 0, 0, 0, 0)
    body = b""
    if bpp <= 8:
        for i in range(1 << bpp):
            r, g, b = palette[i] if i < len(palette) else (0, 0, 0)
            body += bytes((b, g, r, 0))
    xr = (w * bpp + 7) // 8
    for row in reversed(pixels):
        line = bytearray(_pad(xr))
        for x, px in enumerate(row):
            if bpp == 24:
                r, g, b = px[:3]
                line[3 * x:3 * x + 3] = bytes((b, g, r))
            elif bpp == 32:
                r, g, b, a = px
                line[4 * x:4 * x + 4] = bytes((b, g, r, a))
            else:
                bit = x * bpp
                line[bit >> 3] |= px << (8 - bpp - (bit & 7))
        body += line
    if mask_rows is not None:
        mr = _pad((w + 7) // 8)
        for row in reversed(mask_rows):
            line = bytearray(mr)
            for x, v in enumerate(row):
                if v:
                    line[x >> 3] |= 1 << (7 - (x & 7))
            body += line
    img = hdr + body
    if kind == "cursor":
        f1, f2 = hotspot
        entry = struct.pack("<BBBBHHII", w, h, 0, 0, f1, f2, len(img), 22)
        tag = 2
    else:
        entry = struct.pack("<BBBBHHII", w, h, 0, 0, 1, bpp, len(img), 22)
        tag = 1
    return struct.pack("<HHH", 0, tag, 1) + entry + img


def _pad(n):
    return (n + 3) & ~3


class BasicTests(unittest.TestCase):
    def test_single_icon(self):
        out = parse(icon_8bit(32, 32))
        self.assertEqual(out["type"], "icon")
        self.assertEqual(out["images"][0]["bits"], 8)

    def test_not_an_icon(self):
        with self.assertRaises(IcoError):
            parse(b"\x00\x00\x05\x00\x01\x00" + bytes(40))


class CursorTests(unittest.TestCase):
    def test_cursor_type_and_hotspot(self):
        out = parse(build("cursor", 1, 2, 2, [[0, 0], [1, 1]],
                          mask_rows=[[1, 1], [1, 1]], hotspot=(1, 0)))
        self.assertEqual(out["type"], "cursor")
        self.assertEqual(out["images"][0]["hotspot"], [1, 0])
        self.assertEqual(out["images"][0]["bits"], 1)

    def test_icon_has_no_hotspot(self):
        out = parse(build("icon", 8, 2, 2, [[0, 0], [0, 0]]))
        self.assertNotIn("hotspot", out["images"][0])


class TransparencyTests(unittest.TestCase):
    def test_mask_rows_are_top_down(self):
        # Only the top-left pixel is masked out; the mask row is stored last in
        # the DIB, so a decoder that forgets to flip would clear the pixel in
        # the bottom-left corner instead.
        out = parse(build("icon", 8, 2, 2, [[0, 0], [0, 0]],
                          mask_rows=[[1, 0], [0, 0]]))
        top = bytes((0, 0, 0, 0, 0, 0, 0, 255))
        self.assertEqual(out["images"][0]["first_row"], "%08x" % zlib.crc32(top))

    def test_mask_makes_pixels_transparent(self):
        out = parse(build("icon", 8, 2, 2, [[0, 1], [1, 0]],
                          mask_rows=[[1, 0], [0, 1]]))
        self.assertEqual(out["images"][0]["transparent"], 2)

    def test_mask_row_is_dword_aligned(self):
        # A 16-pixel-wide mask is 2 bytes of bits but 4 bytes per row on disk;
        # ignoring the padding shifts every row after the first.
        rows = [[1] * 16 for _ in range(2)]
        out = parse(build("icon", 8, 16, 2, [[0] * 16] * 2, mask_rows=rows))
        self.assertEqual(out["images"][0]["transparent"], 32)

    def test_32bpp_alpha_wins_over_mask(self):
        px = [[(10, 20, 30, 0), (1, 2, 3, 255)], [(4, 5, 6, 255), (7, 8, 9, 128)]]
        out = parse(build("icon", 32, 2, 2, px, mask_rows=[[0, 1], [1, 0]]))
        # Alpha is authoritative, so only the a=0 pixel is transparent.
        self.assertEqual(out["images"][0]["transparent"], 1)

    def test_32bpp_all_zero_alpha_falls_back_to_mask(self):
        out = parse(build("icon", 32, 2, 2, [[(1, 1, 1, 0)] * 2] * 2,
                          mask_rows=[[1, 1], [0, 0]]))
        self.assertEqual(out["images"][0]["transparent"], 2)


class OrientationTests(unittest.TestCase):
    def test_rows_are_top_down(self):
        out = parse(build("icon", 8, 2, 2, [[1, 1], [0, 0]]))
        raw = out["images"][0]
        # first row must be the white (index 1) row stored last in the file
        self.assertEqual(raw["first_row"], "%08x" % zlib.crc32(
            bytes((255, 255, 255, 255)) * 2))

    def test_4bpp_pixels_are_left_to_right(self):
        pal = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
        out = parse(build("icon", 4, 2, 1, [[1, 2]], palette=pal, mask_rows=[[0, 0]]))
        self.assertEqual(out["images"][0]["first_row"], "%08x" % zlib.crc32(
            bytes((255, 0, 0, 255, 0, 255, 0, 255))))

    def test_1bpp_pixels_run_msb_first(self):
        out = parse(build("icon", 1, 8, 1, [[0, 1, 1, 0, 0, 0, 1, 0]],
                          mask_rows=[[0] * 8]))
        black = bytes((0, 0, 0, 255))
        white = bytes((255, 255, 255, 255))
        row = black + white + white + black + black + black + white + black
        self.assertEqual(out["images"][0]["first_row"], "%08x" % zlib.crc32(row))


if __name__ == "__main__":
    unittest.main()
