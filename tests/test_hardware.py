import struct
import unittest

from ngspr.sprites import W, VRAM_WORDS, SpriteError, parse


def solid_tile(color):
    data = bytearray(128)
    for half in (0, 0x40):
        for y in range(16):
            base = half + y * 4
            for plane in range(4):
                data[base + plane] = 0xFF if (color >> plane) & 1 else 0x00
    return bytes(data)


def marker_tile():
    # Only pixel (0, 0) is set (top-left corner), all others transparent.
    data = bytearray(128)
    data[0] = 0x80
    return bytes(data)


class Snapshot:
    def __init__(self, tile_colors):
        self.v = [0] * VRAM_WORDS
        self.pal = [0] * 4096
        self.rom = b"".join(solid_tile(c) for c in tile_colors)

    def sprite(self, n, code, x, y, rows=1, attr=0):
        self.v[0x8000 + n] = 0x0FFF
        self.v[0x8200 + n] = (((0x200 - y) & 0x1FF) << 7) | rows
        self.v[0x8400 + n] = (x & 0x1FF) << 7
        self.v[n * 64] = code
        self.v[n * 64 + 1] = attr

    def chain_link(self, n, code, attr=0):
        # Height zero means this sprite is chained to the current head.
        # Real hardware leaves residual values in the position registers.
        self.v[0x8200 + n] = 0xF800
        self.v[n * 64] = code + 1
        self.v[n * 64 + 1] = attr

    def build(self):
        return (b"NGSPR\0\0\0"
                + struct.pack(">%dH" % VRAM_WORDS, *self.v)
                + struct.pack(">4096H", *self.pal)
                + self.rom)


class SpriteRules(unittest.TestCase):
    def setUp(self):
        # Backdrop black; palette entry 1 white, entry 2 red.
        self.s = Snapshot([1, 2])
        self.s.pal[0xFFF] = 0x0000
        self.s.pal[1] = 0x7FFF
        self.s.pal[2] = 0x000F

    def test_tile_half_and_plane_mapping(self):
        s = Snapshot([])
        s.rom = marker_tile()
        s.pal[0xFFF] = 0x0000
        s.pal[1] = 0x7FFF
        s.sprite(1, 0, 10, 20)
        out = parse(s.build())
        # Exactly one pixel lands at (10, 20); row 0 stays backdrop.
        self.assertEqual(out["sprite_pixels"], 1)
        self.assertEqual(out["first_row"], [["#000000", W]])

    def test_solid_tile_placement(self):
        self.s.sprite(1, 0, 30, 40)
        out = parse(self.s.build())
        self.assertEqual(out["sprites"], 1)
        self.assertEqual(out["sprite_pixels"], 256)
        self.assertEqual(out["colors_used"], 2)
        self.assertEqual(out["first_row"], [["#000000", W]])
        self.assertEqual(out["last_row"], [["#000000", W]])

    def test_vertical_wrap_draws_at_top(self):
        # A sprite starting at playfield row 504, two tiles tall, crosses
        # the 512-row boundary.  Playfield rows 504..511 are off-screen;
        # rows 0..23 wrap onto the top of the screen (24 visible rows).
        self.s.sprite(1, 0, 50, 504, rows=2)
        out = parse(self.s.build())
        self.assertEqual(out["sprite_pixels"], 24 * 16)
        self.assertEqual(out["first_row"][0], ["#000000", 50])
        self.assertEqual(out["first_row"][1], ["#ffffff", 16])

    def test_horizontal_wrap_clips_offscreen_part(self):
        # x=310 keeps columns 310..319; the wrapped part is off the 320 screen.
        self.s.sprite(1, 0, 310, 10)
        out = parse(self.s.build())
        self.assertEqual(out["sprite_pixels"], 10 * 16)
        self.assertEqual(out["row_112"], [["#000000", W]])

    def test_chain_places_links_to_the_right(self):
        self.s.sprite(1, 0, 100, 50, rows=2)
        self.s.chain_link(2, 0)
        self.s.chain_link(3, 0)
        out = parse(self.s.build())
        # One chain head (non-zero height); the two links are part of it.
        self.assertEqual(out["sprites"], 1)
        # Three 16-wide, 32-tall tiles side by side.
        self.assertEqual(out["sprite_pixels"], 3 * 16 * 32)

    def test_vertical_flip_moves_pixel_inside_tile(self):
        # The marker's only pixel is at the tile top-left (0, 0).
        # Vertical flip moves it to intra-tile row 15 without reordering
        # the tiles down the column.
        s = Snapshot([])
        s.rom = marker_tile()
        s.pal[0xFFF] = 0x0000
        s.pal[1] = 0x7FFF
        s.sprite(1, 0, 40, 100, rows=1, attr=0x0002)
        out = parse(s.build())
        self.assertEqual(out["sprite_pixels"], 1)
        # Without flip the pixel would be on row 100; after flip it is on
        # row 115, so neither sampled output row carries it.
        self.assertEqual(out["first_row"], [["#000000", W]])
        self.assertEqual(out["row_112"], [["#000000", W]])
        # And it must still be one pixel (not duplicated), proving the
        # column's tile index was not remapped.
        self.assertEqual(out["colors_used"], 2)

    def test_horizontal_flip_mirrors_within_tile(self):
        s = Snapshot([])
        s.rom = marker_tile()
        s.pal[0xFFF] = 0x0000
        s.pal[1] = 0x7FFF
        # Flipped marker moves its only pixel from column 0 to column 15.
        s.sprite(1, 0, 0, 0, attr=0x0001)
        out = parse(s.build())
        self.assertEqual(out["sprite_pixels"], 1)
        self.assertEqual(out["first_row"],
                         [["#000000", 15], ["#ffffff", 1], ["#000000", W - 16]])

    def test_priority_lower_number_on_top(self):
        self.s.sprite(1, 0, 10, 10)
        self.s.sprite(2, 1, 10, 10)
        out = parse(self.s.build())
        # White sprite 1 covers red sprite 2 completely.
        self.assertEqual(out["sprite_pixels"], 256)
        self.assertEqual(out["colors_used"], 2)

    def test_tile_code_10bit_wraparound(self):
        self.s.sprite(1, 0x400, 10, 10)
        out = parse(self.s.build())
        self.assertEqual(out["missing_tiles"], 0)
        self.assertEqual(out["sprite_pixels"], 256)

    def test_palette_field_only_low_bits(self):
        # Junk above the palette-index bits must not select a palette.
        self.s.sprite(1, 0, 10, 10, attr=0x8000)
        out = parse(self.s.build())
        self.assertEqual(out["sprite_pixels"], 256)

    def test_missing_tile_draws_nothing(self):
        self.s.sprite(1, 50, 10, 10)
        out = parse(self.s.build())
        self.assertEqual(out["missing_tiles"], 1)
        self.assertEqual(out["sprite_pixels"], 0)

    def test_shrunk_sprite_rejected(self):
        self.s.sprite(1, 0, 10, 10)
        self.s.v[0x8000 + 1] = 0x0001
        with self.assertRaises(SpriteError):
            parse(self.s.build())


if __name__ == "__main__":
    unittest.main()
