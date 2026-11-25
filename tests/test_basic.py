import unittest

from zx81kit.dfile import DisplayError, parse

FONT = 512


def dump(*rows, font=None):
    """Build a display file dump: a ZX81DF header, a font, then the rows.

    The display file starts with a HALT and each row is its character codes
    followed by a HALT, exactly as the ZX81 keeps it in memory.
    """
    dfile = b"\x76" + b"".join(bytes(row) + b"\x76" for row in rows)
    font = bytes(font) if font is not None else bytes(FONT)
    return b"ZX81DF" + font + dfile


class BasicTests(unittest.TestCase):
    def test_digits_on_full_screen(self):
        rows = bytes([0x76]) + (bytes([28, 29, 30]) + bytes(29) + b"\x76") * 24
        out = parse(b"ZX81DF" + bytes(512) + rows)
        self.assertEqual(out["text"][0], "012")
        self.assertEqual(out["rows"], 24)

    def test_not_a_dump(self):
        with self.assertRaises(DisplayError):
            parse(b"ZX81XX" + bytes(600))


class CollapsedTests(unittest.TestCase):
    def test_short_rows_are_not_misread(self):
        # A 1K screen: rows shorter than 32 characters, so the old fixed
        # 33-byte stride walked into the middle of the following rows.
        out = dump([0x32, 0x2a, 0x33, 0x3a, 0x0e],                 # "MENU:"
                   [0x1d, 0x00, 0x35, 0x31, 0x26, 0x3e],            # "1 PLAY"
                   [0x1e, 0x00, 0x38, 0x28, 0x34, 0x37, 0x2a, 0x38, 0x00,
                    0x10, 0x39, 0x34, 0x35, 0x00, 0x1d, 0x1c, 0x11])  # "2 SCORES (TOP 10)"
        result = parse(out)
        self.assertEqual(result["text"][:3],
                         ["MENU:", "1 PLAY", "2 SCORES (TOP 10)"])
        self.assertEqual(result["rows"], 3)
        self.assertTrue(result["collapsed"])

    def test_full_rows_are_not_collapsed(self):
        out = dump([28] * 32, [29] * 32)
        result = parse(out)
        self.assertEqual(result["rows"], 2)
        self.assertFalse(result["collapsed"])
        self.assertEqual(result["text"][:2], ["0" * 32, "1" * 32])

    def test_twenty_four_rows_capped(self):
        out = dump(*([[28] * 32] * 30))
        result = parse(out)
        self.assertEqual(result["rows"], 24)
        self.assertEqual(len(result["text"]), 24)


class CharacterSetTests(unittest.TestCase):
    def test_symbols_use_rom_order(self):
        # The punctuation block of the ZX81 character set, codes 11-27.
        result = parse(dump(list(range(11, 28))))
        self.assertEqual(result["text"][0], '"£$:?()><=+-*/;,.')

    def test_digits_and_letters(self):
        codes = list(range(28, 64))          # 36 characters, more than a row
        result = parse(dump(codes[:32], codes[32:]))
        self.assertEqual(result["text"][:2],
                         ["0123456789ABCDEFGHIJKLMNOPQRSTUV",
                          "WXYZ"])

    def test_block_graphics_are_hashes(self):
        result = parse(dump(list(range(1, 11))))
        self.assertEqual(result["text"][0], "#" * 10)


class InverseTests(unittest.TestCase):
    def test_inverse_char_shown_as_normal_form(self):
        # 0x80 + 'A' (0x26) displays an inverse A.
        result = parse(dump([0x80 + 0x26, 0x26]))
        self.assertEqual(result["text"][0], "AA")
        self.assertEqual(result["inverse_chars"], 1)

    def test_inverse_pixels_are_complemented(self):
        font = bytearray(FONT)
        code = 0x26  # 'A'
        font[code * 8:code * 8 + 8] = bytes([0xFF, 0, 0, 0, 0, 0, 0, 0])
        plain = dump([code], font=font)
        inverse = dump([0x80 + code], font=font)
        self.assertEqual(parse(plain)["ink_pixels"], 8)      # one full row
        self.assertEqual(parse(inverse)["ink_pixels"],
                         8 * 8 - 8)                         # rest of the cell
        self.assertEqual(parse(inverse)["inverse_chars"], 1)


class PixelTests(unittest.TestCase):
    def test_first_line_shape(self):
        result = parse(dump([0x26]))  # blank font, so no ink
        self.assertEqual(len(result["first_line"]), 256)
        self.assertEqual(result["ink_pixels"], 0)
        self.assertEqual(result["first_line"], "0" * 256)


if __name__ == "__main__":
    unittest.main()
