import struct
import unittest

from spukit.spu import parse, slot, rgb, widen, SECTION_STARTS, SpuError


def bitmap_with_index(c):
    bm = bytearray(32000)
    for y in range(200):
        for x in range(320):
            p = y * 160 + (x >> 4) * 8
            bit = 15 - (x & 15)
            for pl in range(4):
                if c & (1 << pl):
                    off = p + 2 * pl
                    w = struct.unpack_from(">H", bm, off)[0] | (1 << bit)
                    struct.pack_into(">H", bm, off, w)
    return bytes(bm)


def word(r, g, b):
    return (r << 8) | (g << 4) | b


class Spectrum512Tests(unittest.TestCase):
    def test_widen_and_rgb(self):
        self.assertEqual([widen(v) for v in range(8)], [0, 36, 73, 109, 146, 182, 219, 255])
        self.assertEqual(rgb(0x777), "#ffffff")
        self.assertEqual(rgb(0x000), "#000000")
        self.assertEqual(rgb(0x700), "#ff0000")
        self.assertEqual(rgb(0x070), "#00ff00")
        self.assertEqual(rgb(0x007), "#0000ff")

    def test_slot_sections(self):
        self.assertEqual(SECTION_STARTS, (0, 161, 225))
        self.assertEqual(slot(0, 0), 0)
        self.assertEqual(slot(159, 15), 15)
        self.assertEqual(slot(160, 15), 15)      # last column of section 0
        self.assertEqual(slot(161, 0), 16)       # first column of section 1
        self.assertEqual(slot(224, 15), 31)
        self.assertEqual(slot(225, 0), 32)       # first column of section 2
        self.assertEqual(slot(319, 15), 47)

    def test_line0_is_black_and_line_mapping(self):
        pal = bytearray()
        for r in range(199):
            for section in range(3):
                for c in range(16):
                    pal += struct.pack(">H", word(7, 7, 7))  # all white
        res = parse(bitmap_with_index(3) + bytes(pal))
        self.assertEqual(res["points"]["line_0"], "#000000")
        # line 1 uses block 0 (white), not a black line-0 block
        self.assertEqual(res["points"]["top_left"], "#ffffff")
        self.assertEqual(res["points"]["x1"], "#ffffff")

    def test_section_selection_at_sample_columns(self):
        # block for line 40 (index r = 39): section 0 red, 1 green, 2 blue for slot 3
        pal = bytearray()
        for r in range(199):
            block = [word(0, 0, 0)] * 48
            block[3] = word(7, 0, 0)
            block[16 + 3] = word(0, 7, 0)
            block[32 + 3] = word(0, 0, 7)
            pal += struct.pack(">48H", *block)
        res = parse(bitmap_with_index(3) + bytes(pal))
        # x=160 is still section 0 (red); x=161 is section 1 (green)
        self.assertEqual(res["points"]["x160"], "#ff0000")
        self.assertEqual(res["points"]["x161"], "#00ff00")
        # bottom right (319, 199) resolves to section 2 of the last block
        self.assertEqual(res["points"]["bottom_right"], "#0000ff")

    def test_colors_shown_and_palette_changes(self):
        pal = bytearray()
        base = [word(1, 2, 3)] * 16 + [word(4, 5, 6)] * 16 + [word(7, 0, 1)] * 16
        for r in range(199):
            pal += struct.pack(">48H", *base)
        res = parse(bitmap_with_index(3) + bytes(pal))
        self.assertEqual(res["colors_shown"], 3)
        self.assertEqual(res["palette_changes"], 0)

        pal2 = bytearray(pal)
        struct.pack_into(">H", pal2, 49 * 96, word(7, 7, 7))  # alter line 50
        res2 = parse(bitmap_with_index(3) + bytes(pal2))
        self.assertEqual(res2["palette_changes"], 2)

    def test_examples(self):
        for name, csh, pch in (("plasma", 128, 15), ("logo", 2, 0)):
            with open("examples/%s.spu" % name, "rb") as fh:
                r = parse(fh.read())
            self.assertEqual(r["colors_shown"], csh)
            self.assertEqual(r["palette_changes"], pch)
            self.assertEqual(r["points"]["line_0"], "#000000")

    def test_bad_size(self):
        with self.assertRaises(SpuError):
            parse(b"\x00" * 10)


if __name__ == "__main__":
    unittest.main()
