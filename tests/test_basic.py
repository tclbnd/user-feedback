import unittest

from hgrkit.hgr import line_offset, parse

BLANK = bytes(8192)


def screen(off, b, size=8192):
    d = bytearray(size)
    d[off] = b
    return parse(bytes(d))


class BasicTests(unittest.TestCase):
    def test_blank(self):
        out = parse(BLANK)
        self.assertEqual((out["lit_pixels"], out["group1_bytes"]), (0, 0))

    def test_black(self):
        self.assertEqual(parse(BLANK)["points"]["center"], "black")


class HardwareTests(unittest.TestCase):
    def test_line_addressing(self):
        # three interleaved groups of 64, 8 and 1 lines
        self.assertEqual(line_offset(1), 0x400)
        self.assertEqual(line_offset(8), 0x80)
        self.assertEqual(line_offset(64), 0x28)
        self.assertEqual(line_offset(191), 0x1FD0)

    def test_row_1_reads_its_own_byte(self):
        out = screen(0x400, 0x01)
        self.assertEqual(out["points"]["row_1"], "violet")
        self.assertEqual(out["points"]["top_left"], "black")

    def test_bit_zero_is_leftmost(self):
        out = screen(0, 0x01)
        self.assertEqual(out["points"]["top_left"], "violet")
        out = screen(0, 0x40)
        self.assertEqual(out["points"]["top_left"], "black")
        self.assertEqual(out["lit_pixels"], 1)

    def test_x7_is_first_bit_of_second_byte(self):
        out = screen(1, 0x01)
        self.assertEqual(out["points"]["x7"], "green")  # odd column, group bit clear

    def test_group_bit_colours(self):
        self.assertEqual(screen(0, 0x81)["points"]["top_left"], "blue")
        self.assertEqual(screen(0, 0x82)["colors"], {"orange": 1})
        self.assertEqual(screen(0, 0x01)["colors"], {"violet": 1})
        self.assertEqual(screen(0, 0x02)["colors"], {"green": 1})

    def test_adjacent_lit_pixels_are_white(self):
        self.assertEqual(screen(0, 0x03)["colors"], {"white": 2})
        self.assertEqual(screen(0, 0x83)["colors"], {"white": 2})

    def test_short_bsave(self):
        out = screen(0, 0x01, size=8184)
        self.assertEqual(out["bytes"], 8184)
        self.assertEqual(out["points"]["top_left"], "violet")

    def test_hole_bytes(self):
        # 0x78 is the first byte past the 120 displayed bytes of a 128-byte row group
        out = screen(0x78, 0x55)
        self.assertEqual(out["hole_bytes"], 1)
        self.assertEqual(out["lit_pixels"], 0)
        # every one of the 512 hole offsets counts
        shown = {line_offset(y) + i for y in range(192) for i in range(40)}
        d = bytearray(8192)
        for off in range(8192):
            if off not in shown:
                d[off] = 1
        self.assertEqual(parse(bytes(d))["hole_bytes"], 512)
