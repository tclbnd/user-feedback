import unittest

from mo5scr.screen import Mo5Error, parse


def snapshot(form=b"", colour=b"", reg=0):
    hdr = b"MO5SCRN\0" + bytes([reg]) + bytes(7)
    return hdr + form.ljust(8192, b"\0")[:8192] + colour.ljust(8192, b"\0")[:8192]


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        out = parse(b"MO5SCRN\0" + bytes(8) + bytes(16384))
        self.assertEqual(out["first_row"], [["0", 320]])
        self.assertEqual(out["border"], "0")

    def test_not_a_snapshot(self):
        with self.assertRaises(Mo5Error):
            parse(b"MO5SCRX\0" + bytes(100))

    def test_border_is_high_nibble_of_register(self):
        # $A7C0: border in bits 7-4, control flags in bits 3-0.
        data = snapshot(reg=0x59)
        self.assertEqual(parse(data)["border"], "5")

    def test_form_plane_comes_first(self):
        form = bytes([0x80]) + bytes(39)
        colour = bytes([0xF1]) + bytes(39)
        out = parse(snapshot(form=form, colour=colour))
        # First cell: bit 7 set -> one foreground pixel, then 7 background.
        self.assertEqual(out["first_row"], [["f", 1], ["1", 7], ["0", 312]])
        self.assertEqual(out["foreground_pixels"], 1)

    def test_ink_is_high_nibble(self):
        form = bytes([0xFF]) + bytes(39)
        colour = bytes([0x2A]) + bytes(39)
        out = parse(snapshot(form=form, colour=colour))
        self.assertEqual(out["first_row"], [["2", 8], ["0", 312]])

    def test_form_bits_are_most_significant_first(self):
        form = bytes([0x01]) + bytes(39)
        out = parse(snapshot(form=form, colour=bytes([0xF0]) + bytes(39)))
        # Only the rightmost pixel of the cell uses the ink.
        self.assertEqual(out["first_row"], [["0", 7], ["f", 1], ["0", 312]])

    def test_only_first_192_lines_displayed(self):
        # The 200 x 40 picture uses the first 8000 bytes; the trailing 192
        # bytes of each plane must be ignored even if they hold data.
        form = bytes(8000) + bytes([0xFF] * 192)
        colour = bytes(8000) + bytes([0xF0] * 192)
        out = parse(snapshot(form=form, colour=colour))
        self.assertEqual(out["colors_used"], 1)
        self.assertEqual(out["foreground_pixels"], 0)

    def test_incomplete_when_colour_plane_missing(self):
        data = b"MO5SCRN\0" + bytes(8) + bytes(8192)
        out = parse(data)
        self.assertFalse(out["complete"])
        self.assertIsNone(out["first_row"])


if __name__ == "__main__":
    unittest.main()
