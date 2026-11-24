import unittest

from koalakit.koala import NAMES, colour, parse

BLANK = bytes(10001)
BG, HI, LO, CRAM = 0, 2, 3, 4          # colour numbers used by the test pictures


def encode(grid, background=BG, screen=None, colour_ram=None):
    """Build a 10001-byte Koala body from a 200 x 160 grid of colour numbers.

    Uses the Koala Painter / VIC-II multicolour rules: the bitmap is stored
    character cell by character cell, the leftmost pixel of a byte is bits 7-6,
    bit-pair 01 takes the high nibble of screen RAM, 10 the low nibble and 11
    the lower nibble of colour RAM.
    """
    if screen is None:
        screen = [((x // 4) % 16, (x // 4 + 5) % 16) for x in range(1000)]
    if colour_ram is None:
        colour_ram = [(x // 4 + 9) % 16 for x in range(1000)]
    body = bytearray(10001)
    for y in range(200):
        for x in range(160):
            cell = (y // 8) * 40 + x // 4
            wanted = grid[y][x]
            if wanted == background:
                bits = 0
            elif wanted == screen[cell][0]:
                bits = 1
            elif wanted == screen[cell][1]:
                bits = 2
            elif wanted == colour_ram[cell]:
                bits = 3
            else:
                raise ValueError("colour %d not available in cell %d" % (wanted, cell))
            offset = ((y // 8) * 40 + x // 4) * 8 + (y % 8)
            body[offset] |= bits << (6 - 2 * (x % 4))
    body[8000:9000] = bytes((hi << 4) | lo for hi, lo in screen)
    body[9000:10000] = bytes(colour_ram)
    body[10000] = background
    return bytes(body)


class BasicTests(unittest.TestCase):
    def test_background(self):
        self.assertEqual(parse(BLANK)["background"], "black")

    def test_bad_size(self):
        with self.assertRaises(ValueError):
            parse(bytes(500))

    def test_ambiguous_sizes_are_rejected(self):
        for size in (10000, 10002, 10007, 20000):
            with self.assertRaises(ValueError):
                parse(bytes(size))


class LayoutTests(unittest.TestCase):
    def test_bitmap_is_stored_per_character_cell(self):
        # Byte 0 starts the first cell; its bytes 0-7 are the eight scanlines of
        # that cell, so pixel (0, 1) lives at byte 1, not byte 40.
        body = bytearray(10001)
        body[0] = 0xFF                 # bits 11: pixel (0..3, 0) use colour RAM
        body[1] = 0x00                 # background on the cell's second scanline
        body[9000] = CRAM
        self.assertEqual([colour(body, x, 0) for x in range(4)], [CRAM] * 4)
        self.assertEqual([colour(body, x, 1) for x in range(4)], [BG] * 4)
        # Colour RAM is indexed by cell: cell 40 is the first cell of row 8.
        body[320] = 0xFF
        body[9040] = CRAM
        self.assertEqual([colour(body, x, 8) for x in range(4)], [CRAM] * 4)
        self.assertEqual([colour(body, x, 7) for x in range(4)], [BG] * 4)

    def test_adjacent_cells_are_eight_bytes_apart(self):
        body = bytearray(10001)
        body[0] = 0xFF                  # cell (0, 0) first scanline, pixel x=0..3
        body[8] = 0xFF                  # cell (1, 0) first scanline, pixel x=4..7
        body[9000] = CRAM
        body[9001] = CRAM
        self.assertEqual([colour(body, x, 0) for x in range(8)], [CRAM] * 8)

    def test_leftmost_pixel_is_the_high_bit_pair(self):
        body = bytearray(10001)
        body[0] = 0b00011011           # 00, 01, 10, 11
        body[8000] = (HI << 4) | LO
        body[9000] = CRAM
        self.assertEqual([colour(body, x, 0) for x in range(4)], [BG, HI, LO, CRAM])

    def test_screen_ram_nibbles(self):
        body = bytearray(10001)
        body[0] = 0b01010000           # 01, 01, 00, 00
        body[1] = 0b10101010           # 10, 10, 10, 10 on the cell's next scanline
        body[8000] = 0xA5              # high nibble -> 01, low nibble -> 10
        self.assertEqual([colour(body, x, 0) for x in range(4)], [0xA, 0xA, BG, BG])
        self.assertEqual([colour(body, x, 1) for x in range(4)], [5, 5, 5, 5])

    def test_colour_ram_uses_the_low_nibble(self):
        body = bytearray(10001)
        body[0] = 0xFF
        body[9000] = 0xF3
        self.assertEqual([colour(body, x, 0) for x in range(4)], [3, 3, 3, 3])


class RoundTripTests(unittest.TestCase):
    def test_grid_round_trip(self):
        screen = [((x // 4) % 16, (x // 4 + 5) % 16) for x in range(1000)]
        cram = [(x // 4 + 9) % 16 for x in range(1000)]
        grid = [[BG] * 160 for _ in range(200)]
        for y in range(200):
            for x in range(160):
                cell = (y // 8) * 40 + x // 4
                grid[y][x] = (BG, screen[cell][0], screen[cell][1], cram[cell])[(x + y) % 4]
        body = encode(grid, BG, screen, cram)
        self.assertEqual(len(body), 10001)
        for y in range(200):
            for x in range(160):
                self.assertEqual(colour(body, x, y), grid[y][x], (x, y))

    def test_points_follow_the_grid(self):
        screen = [((x // 4) % 16, (x // 4 + 5) % 16) for x in range(1000)]
        cram = [(x // 4 + 9) % 16 for x in range(1000)]
        grid = [[BG] * 160 for _ in range(200)]
        for y in range(200):
            for x in range(160):
                cell = (y // 8) * 40 + x // 4
                grid[y][x] = (BG, screen[cell][0], screen[cell][1], cram[cell])[(x + y) % 4]
        result = parse(encode(grid, BG, screen, cram))
        self.assertIsNone(result["load_address"])
        self.assertEqual(result["background"], NAMES[BG])
        for name, (x, y) in {"top_left": (0, 0), "top_right": (159, 0), "row_7": (0, 7), "row_8": (0, 8),
                             "center": (80, 100), "bottom_right": (159, 199)}.items():
            self.assertEqual(result["points"][name], NAMES[grid[y][x]])


class LoadAddressTests(unittest.TestCase):
    def test_load_address_is_read_and_skipped(self):
        body = encode([[BG] * 160 for _ in range(200)])
        body = bytearray(body)
        body[10000] = 6
        data = b"\x00\x60" + bytes(body)
        self.assertEqual(len(data), 10003)
        result = parse(data)
        self.assertEqual(result["load_address"], "$6000")
        self.assertEqual(result["background"], "blue")

    def test_trailing_padding(self):
        body = encode([[BG] * 160 for _ in range(200)])
        for pad in (b"", b"\x00", b"\xde\xad", b"\x01\x02\x03"):
            data = b"\x00\x60" + body + pad
            self.assertEqual(parse(data)["load_address"], "$6000")
            self.assertEqual(parse(data)["background"], NAMES[BG])

    def test_padded_load_address_sizes(self):
        body = encode([[BG] * 160 for _ in range(200)])
        for size in (10003, 10004, 10005, 10006):
            data = (b"\x00\x20" + body)[:size] + bytes(max(0, size - 10003))
            self.assertEqual(len(data), size)
            self.assertEqual(parse(data)["load_address"], "$2000")


class ExampleTests(unittest.TestCase):
    def test_sunset(self):
        with open("examples/sunset.koa", "rb") as fh:
            result = parse(fh.read())
        self.assertEqual(result["load_address"], "$6000")
        self.assertEqual(result["background"], "blue")

    def test_logo(self):
        with open("examples/logo.koa", "rb") as fh:
            result = parse(fh.read())
        self.assertIsNone(result["load_address"])
        self.assertEqual(result["background"], "black")
