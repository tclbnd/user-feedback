import unittest

from orickit.hires import PER_LINE, HEIGHT, HiresError, parse

BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)


def line(*cells):
    """Build one 40-byte screen line, padding with graphics byte 0x00.

    Padding with a graphics byte keeps the current ink/paper state untouched
    (an attribute pad byte would itself count and recolour the rest of the
    line).
    """
    row = list(cells)
    assert len(row) <= PER_LINE
    return bytes(row + [0x00] * (PER_LINE - len(row)))


class AttributeTests(unittest.TestCase):
    def test_attribute_nibbles_and_cell_fill(self):
        # Put the single attribute last so the graphics padding before it keeps
        # the power-on black paper and cannot add green pixels.
        # 0x42: nibble A = 2 (paper green), nibble B = 0 (ink black).
        out = parse(bytes([0x00] * (PER_LINE - 1) + [0x42]))
        self.assertEqual(out["attribute_bytes"], 1)
        # The attribute cell paints its six pixels with the paper colour.
        self.assertEqual(out["first_line"][-6:], "222222")
        self.assertEqual(out["histogram"][GREEN], 6)
        # All other cells stay paper black.
        self.assertEqual(out["histogram"][BLACK], 240 - 6)

    def test_graphics_byte_uses_ink_and_paper(self):
        # Paper green (2), ink red (1); graphics 0x3F sets all six pixels.
        out = parse(line(0x42 | (RED << 3), 0x3F))
        self.assertEqual(out["first_line"][:6], "222222")    # attribute slot
        self.assertEqual(out["first_line"][6:12], "111111")  # ink on set bits
        self.assertEqual(out["histogram"][RED], 6)

    def test_graphics_clear_bits_take_paper(self):
        # Ink white (7), paper black (0): graphics 0x00 -> all paper.
        out = parse(line(0x40 | (WHITE << 3), 0x00))
        self.assertEqual(out["first_line"][6:12], "000000")

    def test_graphics_bit7_is_ignored(self):
        with_top = parse(line(0x40, 0x80))
        without_top = parse(line(0x40, 0x00))
        self.assertEqual(with_top["histogram"], without_top["histogram"])
        self.assertEqual(with_top["first_line"], without_top["first_line"])

    def test_pixel_order_msb_first(self):
        # 0x20 = 100000b: only the leftmost of the six pixels is ink.
        out = parse(line(0x40 | (RED << 3), 0x20))
        self.assertEqual(out["first_line"][6:12], "100000")


class InverseTests(unittest.TestCase):
    # A line whose attribute is placed last: the preceding cells stay in the
    # default (non-inverse) state, so inverse_cells counts only the cells we
    # explicitly place after the inverse attribute.
    @staticmethod
    def line_with_inverse(*after_c5):
        prefix = PER_LINE - 1 - len(after_c5)
        return bytes([0x00] * prefix + [0xC5] + list(after_c5))

    def test_inverse_attribute_swaps_colours(self):
        # 0xC5: A = 5 magenta, B = 0 black, inverse -> ink magenta, paper black.
        out = parse(self.line_with_inverse(0x3F))
        self.assertEqual(out["attribute_bytes"], 1)
        start = (PER_LINE - 2) * 6
        # Attribute cell fills with the new paper = black.
        self.assertEqual(out["first_line"][start:start + 6], "000000")
        # Inverted graphics: set pixels take paper (black), clear take ink.
        self.assertEqual(out["first_line"][start + 6:start + 12], "000000")
        self.assertEqual(out["inverse_cells"], 1)

    def test_inverse_mixes_ink_and_paper(self):
        # Inverse: paper black (0), ink magenta (5); 0x20 = 100000b.
        out = parse(self.line_with_inverse(0x20))
        start = (PER_LINE - 2) * 6
        # First pixel set -> paper black, rest clear -> ink magenta.
        self.assertEqual(out["first_line"][start + 6:start + 12], "055555")
        self.assertEqual(out["inverse_cells"], 1)

    def test_inverse_latches_until_next_attribute(self):
        # Two graphics cells after the inverse attribute are both inverted.
        out = parse(self.line_with_inverse(0x20, 0x00))
        self.assertEqual(out["inverse_cells"], 2)
        # A normal attribute (bit 7 clear) turns inverse off again.
        # 0x48: nibble A = 0 (paper black), nibble B = 1 (ink red).
        # The following 0x20 then renders normally: one red set pixel, five
        # black paper pixels.
        prefix = PER_LINE - 4
        data2 = bytes([0x00] * prefix + [0xC5, 0x20, 0x48, 0x20])
        out2 = parse(data2)
        self.assertEqual(out2["inverse_cells"], 1)
        self.assertEqual(out2["first_line"][-6:], "100000")

    def test_attribute_cell_not_counted_as_inverse(self):
        # The inverse attribute is the last cell; nothing follows it.
        out = parse(bytes([0x00] * (PER_LINE - 1) + [0xC5]))
        self.assertEqual(out["attribute_bytes"], 1)
        self.assertEqual(out["inverse_cells"], 0)


class StateTests(unittest.TestCase):
    def test_colours_persist_across_lines(self):
        # Attribute sets paper green (2), ink red (1); graphics 0x3F all set.
        data = line(0x42 | (RED << 3), 0x3F) + line(0x3F)
        out = parse(data)
        self.assertEqual(out["lines"], 2)
        # Second line has no attribute, so it keeps ink red / paper green.
        self.assertEqual(out["last_line"][:6], "111111")
        # Six red pixels on each of the two lines.
        self.assertEqual(out["histogram"][RED], 12)

    def test_frame_starts_white_ink_black_paper(self):
        # No attributes at all: set pixels default to white ink.
        out = parse(line(0x20))
        self.assertEqual(out["first_line"][:6], "700000")
        self.assertEqual(out["histogram"][WHITE], 1)
        self.assertEqual(out["attribute_bytes"], 0)
        self.assertEqual(out["inverse_cells"], 0)

    def test_all_eight_colours(self):
        # The eight attributes 0x40..0x47 step the paper nibble through colours
        # 0..7.  Put them in the last eight cells with no graphics after them,
        # so each colour appears exactly six times (its own attribute cell).
        attrs = bytes(0x40 + c for c in range(8))
        out = parse(bytes([0x00] * (PER_LINE - 8)) + attrs)
        self.assertEqual(out["colors_used"], 8)
        # Colours 1..7 each appear only inside their own attribute cell.
        for c in range(1, 8):
            self.assertEqual(out["histogram"][c], 6)
        # Black additionally fills the default-paper graphics prefix.
        self.assertEqual(out["histogram"][BLACK], 6 + (PER_LINE - 8) * 6)


class FileShapeTests(unittest.TestCase):
    def test_truncated_dump_keeps_whole_lines(self):
        data = bytes([0x40]) * (PER_LINE * 2 + 7)
        out = parse(data)
        self.assertEqual(out["lines"], 2)
        self.assertFalse(out["complete"])
        self.assertEqual(len(out["first_line"]), 240)
        self.assertEqual(len(out["last_line"]), 240)
        self.assertEqual(sum(out["histogram"]), 240 * 2)

    def test_complete_screen(self):
        out = parse(bytes([0x40]) * PER_LINE * HEIGHT)
        self.assertTrue(out["complete"])
        self.assertEqual(out["lines"], HEIGHT)
        self.assertEqual(sum(out["histogram"]), 240 * HEIGHT)

    def test_empty_and_oversized_rejected(self):
        with self.assertRaises(HiresError):
            parse(b"")
        with self.assertRaises(HiresError):
            parse(b"\x40" * (PER_LINE * HEIGHT + 1))
        with self.assertRaises(HiresError):
            parse(b"\x01\x02")  # not even one whole line

    def test_histogram_totals_pixels(self):
        out = parse(bytes([0x40]) * PER_LINE * 3)
        self.assertEqual(sum(out["histogram"]), 240 * 3)


if __name__ == "__main__":
    unittest.main()
