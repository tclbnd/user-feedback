import unittest

from vdckit.screen import VdcError, parse


def snap(row):
    r = [0] * 37
    r[1], r[6], r[9], r[26] = 1, 1, 7, 0xF0
    return b"VDCSNAP\x00" + bytes(r) + bytes([0]) + bytes([0]) + bytes([row] * 8 + [0] * 8) + bytes(511 * 16)


def build(cols, rows, r9=7, r25=0, r26=0xF0, screen=None, attrs=None, glyphs=None):
    """Assemble a VDCSNAP file.  ``glyphs`` maps a (code, scanline) to a byte."""
    r = [0] * 37
    r[1], r[6], r[9], r[25], r[26] = cols, rows, r9, r25, r26
    n = cols * rows
    screen = list(screen if screen is not None else [0] * n)
    attrs = list(attrs if attrs is not None else [0] * n)
    gen = bytearray(512 * 16)
    for key, bits in (glyphs or {}).items():
        gen[key[0] * 16 + key[1]] = bits
    return (b"VDCSNAP\x00" + bytes(r)
            + bytes(screen) + bytes(attrs) + bytes(gen))


class BasicTests(unittest.TestCase):
    def test_single_cell(self):
        self.assertEqual(parse(snap(0xF0))["first_line"], "ffff0000")

    def test_not_a_snapshot(self):
        with self.assertRaises(VdcError):
            parse(b"VDCSNAPX" + bytes(40))


class HeightTests(unittest.TestCase):
    def test_ten_line_characters(self):
        out = parse(build(1, 1, r9=9, glyphs={(0, 0): 0xFF, (0, 9): 0xFF}))
        self.assertEqual(out["char_height"], 10)
        self.assertEqual(out["height"], 10)
        # Both the first and the last scan line of the glyph are shown.
        self.assertEqual(out["histogram"][0xF], 16)

    def test_twelve_line_characters(self):
        out = parse(build(2, 1, r9=11, glyphs={(0, 0): 0xF0, (0, 11): 0x0F}))
        self.assertEqual(out["char_height"], 12)
        self.assertEqual(out["height"], 12)
        self.assertEqual(out["width"], 16)
        self.assertEqual(sum(out["histogram"]), 2 * 12 * 8)


class AttributeTests(unittest.TestCase):
    def test_attributes_off_follows_global_colour(self):
        # R25 bit 6 clear: attribute bytes are ignored and the foreground
        # comes from the high nibble of R26.
        out = parse(build(1, 1, r25=0, r26=0x2A, attrs=[0xFF],
                          glyphs={(0, y): 0xFF for y in range(8)}))
        self.assertEqual(out["first_line"], "22222222")
        self.assertEqual(out["colors_used"], 1)
        self.assertEqual(out["reverse_cells"], 0)
        self.assertEqual(out["underline_cells"], 0)
        self.assertEqual(out["blink_cells"], 0)
        self.assertEqual(out["alternate_cells"], 0)

    def test_reverse_swaps_foreground_and_background(self):
        out = parse(build(1, 1, r25=0x40, r26=0x21, attrs=[0x43],
                          glyphs={(0, 0): 0xFF}))
        self.assertEqual(out["reverse_cells"], 1)
        # Attribute foreground 3 on global background 1; reversed gives ink 1.
        self.assertEqual(out["first_line"], "11111111")
        self.assertEqual(out["histogram"][1], 8)

    def test_underline_draws_last_scan_line(self):
        out = parse(build(1, 1, r25=0x40, r26=0x10, attrs=[0x25],
                          glyphs={(0, 0): 0x00}))
        self.assertEqual(out["underline_cells"], 1)
        self.assertEqual(out["first_line"], "00000000")
        # Seven plain background lines plus a solid foreground underline.
        self.assertEqual(out["histogram"][5], 8)
        self.assertEqual(out["histogram"][0], 56)

    def test_blink_is_counted_and_shown_in_on_phase(self):
        out = parse(build(1, 1, r25=0x40, r26=0x10, attrs=[0x15],
                          glyphs={(0, 0): 0xFF}))
        self.assertEqual(out["blink_cells"], 1)
        self.assertEqual(out["first_line"], "55555555")

    def test_alternate_character_set(self):
        # Code 1 is blank in the first set and solid in the second set.
        out = parse(build(1, 1, r25=0x40, r26=0x10, screen=[1], attrs=[0x82],
                          glyphs={(257, 0): 0xFF}))
        self.assertEqual(out["alternate_cells"], 1)
        self.assertEqual(out["first_line"], "22222222")

    def test_attribute_foreground_colours(self):
        out = parse(build(2, 1, r25=0x40, r26=0x10, attrs=[0x0A, 0x0C],
                          glyphs={(0, y): 0xFF for y in range(8)}))
        self.assertEqual(out["first_line"], "aaaaaaaa" + "cccccccc")
        self.assertEqual(out["colors_used"], 2)

    def test_counts_are_per_cell(self):
        out = parse(build(3, 1, r25=0x40, r26=0x10,
                          attrs=[0x50, 0xA0, 0x00], glyphs={(0, 0): 0xFF}))
        self.assertEqual(out["reverse_cells"], 1)
        self.assertEqual(out["underline_cells"], 1)
        self.assertEqual(out["blink_cells"], 1)
        self.assertEqual(out["alternate_cells"], 1)
