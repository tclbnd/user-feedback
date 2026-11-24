import os
import struct
import unittest

from koalakit.koala import colour, parse

BLANK = bytes(10001)
HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, os.pardir, "examples")


def put_bits(body, x, y, bits):
    """Write one multicolour bit pair at pixel (x, y) using the VIC-II layout."""
    body[(y // 8) * 320 + (x // 4) * 8 + y % 8] |= bits << (6 - 2 * (x % 4))


def put_screen(body, x, y, byte):
    body[8000 + (y // 8) * 40 + x // 4] = byte


def put_colour(body, x, y, byte):
    body[9000 + (y // 8) * 40 + x // 4] = byte


BODY_SHAPE = 10001


def canvas(background=0):
    body = bytearray(BODY_SHAPE)
    body[10000] = background
    return body


class ColourTests(unittest.TestCase):
    def test_bit_pairs_pick_the_right_colour_source(self):
        body = canvas(background=6)
        put_screen(body, 0, 0, 0x23)      # high nibble 2, low nibble 3
        put_colour(body, 0, 0, 0x0E)
        for x, bits in enumerate((0, 1, 2, 3)):
            put_bits(body, x, 0, bits)
        self.assertEqual([colour(body, x, 0) for x in range(4)], [6, 2, 3, 14])

    def test_screen_ram_high_and_low_nibbles(self):
        body = canvas(background=0)
        put_screen(body, 0, 0, 0xA5)
        put_bits(body, 0, 0, 1)
        put_bits(body, 1, 0, 2)
        self.assertEqual(colour(body, 0, 0), 0x0A)
        self.assertEqual(colour(body, 1, 0), 0x05)

    def test_colour_ram_uses_low_nibble(self):
        body = canvas(background=0)
        put_colour(body, 0, 0, 0xFC)
        put_bits(body, 0, 0, 3)
        self.assertEqual(colour(body, 0, 0), 0x0C)

    def test_pixels_go_left_to_right(self):
        # 00 01 10 11 across one byte must read as four pixels in that order.
        body = canvas(background=1)
        body[0] = 0b00011011
        put_screen(body, 0, 0, 0x23)
        put_colour(body, 0, 0, 0x0E)
        self.assertEqual([colour(body, x, 0) for x in range(4)], [1, 2, 3, 14])

    def test_pixel_columns_do_not_depend_on_scanline_order(self):
        # body[8] is the first byte of the second cell of row 0, not row 1.
        body = canvas(background=1)
        body[8] = 0b01000000
        put_screen(body, 4, 0, 0x90)
        self.assertEqual(colour(body, 4, 0), 9)
        self.assertEqual(colour(body, 0, 1), 1)       # body[1] is still empty

    def test_scanlines_of_a_cell_are_interleaved_per_cell(self):
        body = canvas(background=1)
        body[1] = 0b11000000                      # row 1 of cell (0, 0)
        put_colour(body, 0, 1, 0x0D)
        self.assertEqual(colour(body, 0, 1), 13)
        self.assertEqual(colour(body, 0, 0), 1)   # row 0 is untouched

    def test_each_character_row_is_320_bytes(self):
        body = canvas(background=1)
        body[320] = 0b01000000                    # first byte of row 8
        put_screen(body, 0, 8, 0x70)
        self.assertEqual(colour(body, 0, 8), 7)
        self.assertEqual(colour(body, 0, 0), 1)
        self.assertEqual(colour(body, 0, 7), 1)

    def test_last_cell_of_last_row(self):
        body = canvas(background=1)
        body[7999] = 0b11000000                   # y = 199, x = 156..159
        put_colour(body, 156, 199, 0x0F)
        self.assertEqual(colour(body, 156, 199), 15)


class ParseTests(unittest.TestCase):
    def test_background(self):
        self.assertEqual(parse(BLANK)["background"], "black")
        body = canvas(background=10)
        self.assertEqual(parse(bytes(body))["background"], "light_red")

    def test_background_pixels_follow_background_byte(self):
        body = canvas(background=9)
        result = parse(bytes(body))
        self.assertEqual(result["most_common"], "brown")
        self.assertEqual(result["colors_used"], ["brown"])
        self.assertEqual(set(result["points"].values()), {"brown"})

    def test_bad_size(self):
        for size in (0, 500, 10000, 10002, 10007):
            with self.assertRaises(ValueError):
                parse(bytes(size))

    def test_no_load_address(self):
        self.assertIsNone(parse(BLANK)["load_address"])

    def test_load_address(self):
        data = struct.pack("<H", 0x6000) + BLANK
        self.assertEqual(parse(data)["load_address"], "$6000")
        self.assertEqual(parse(data)["background"], "black")

    def test_load_address_is_big_endian_in_the_file(self):
        # 00 60 is $6000 little-endian; a file starting with 60 00 is $0060.
        data = bytes.fromhex("6000") + BLANK
        self.assertEqual(parse(data)["load_address"], "$0060")

    def test_padding_after_the_body(self):
        for padding in range(4):
            data = struct.pack("<H", 0x6000) + BLANK + bytes(padding)
            result = parse(data)
            self.assertEqual(result["load_address"], "$6000")
            self.assertEqual(result["background"], "black")

    def test_load_address_body_is_not_shifted(self):
        body = canvas(background=2)
        body[0] = 0b01000000
        put_screen(body, 0, 0, 0x80)
        plain = parse(bytes(body))
        loaded = parse(struct.pack("<H", 0x6000) + bytes(body))
        for key in ("background", "points", "colors_used", "most_common"):
            self.assertEqual(plain[key], loaded[key], key)
        self.assertEqual(loaded["points"]["top_left"], "orange")

    def test_points_are_on_the_160_by_200_grid(self):
        body = canvas(background=0)
        put_screen(body, 0, 0, 0x13)       # shared by (0, 0) and (0, 7)
        put_bits(body, 0, 0, 1)            # high nibble 1
        put_bits(body, 0, 7, 2)            # low nibble 3
        put_bits(body, 159, 0, 2)
        put_screen(body, 159, 0, 0x02)
        for (x, y), name in (((0, 8), 4), ((80, 100), 5), ((159, 199), 6)):
            put_bits(body, x, y, 3)
            put_colour(body, x, y, name)
        result = parse(bytes(body))
        self.assertEqual(result["points"], {"top_left": "white", "top_right": "red", "row_7": "cyan",
                                           "row_8": "purple", "center": "green", "bottom_right": "blue"})
        self.assertEqual(result["colors_used"], ["black", "white", "red", "cyan", "purple", "green", "blue"])
        self.assertEqual(result["most_common"], "black")

    def test_most_common_breaks_ties_by_lower_colour_number(self):
        body = canvas(background=0)
        body[:8000] = b"\xff" * 8000        # every pixel uses colour RAM
        for cell in range(1000):
            body[9000 + cell] = 5 if cell % 2 else 2     # green / red, 16000 pixels each
        result = parse(bytes(body))
        self.assertEqual(result["most_common"], "red")
        self.assertEqual(result["colors_used"], ["red", "green"])


class ExampleTests(unittest.TestCase):
    def load(self, name):
        with open(os.path.join(EXAMPLES, name), "rb") as fh:
            return fh.read()

    def test_logo_has_no_load_address(self):
        result = parse(self.load("logo.koa"))
        self.assertIsNone(result["load_address"])
        self.assertEqual(result["background"], "black")
        self.assertEqual(len(result["colors_used"]), 16)

    def test_sunset_has_load_address_and_bands(self):
        result = parse(self.load("sunset.koa"))
        self.assertEqual(result["load_address"], "$6000")
        self.assertEqual(result["background"], "blue")
        self.assertEqual(result["points"]["top_left"], "blue")
        self.assertEqual(result["points"]["row_7"], "blue")
        self.assertEqual(result["points"]["row_8"], "blue")
        self.assertEqual(result["points"]["center"], "orange")
        self.assertEqual(result["points"]["bottom_right"], "brown")
        self.assertEqual(result["colors_used"], ["blue", "yellow", "orange", "brown"])
        self.assertEqual(result["most_common"], "blue")

    def test_load_address_is_stripped_before_decoding(self):
        for name in ("logo.koa", "sunset.koa"):
            data = self.load(name)
            body = data[2:] if len(data) >= 10003 else data
            self.assertEqual(len(body), 10001)
            result = parse(data)
            plain = parse(body)
            for key in ("background", "points", "colors_used", "most_common"):
                self.assertEqual(result[key], plain[key], key)


if __name__ == "__main__":
    unittest.main()
