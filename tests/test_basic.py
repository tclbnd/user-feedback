import struct
import unittest

from gbcbg.screen import BgError, parse

BLACK = "#000000"
RED = "#ff0000"


def tile(color):
    """A tile whose 64 pixels all use colour index `color`."""
    lo = 0xFF if color & 1 else 0x00
    hi = 0xFF if color & 2 else 0x00
    return bytes([lo, hi]) * 8


def snapshot(lcdc=0x91, scx=0, scy=0, palette=(), bank0=(), bank1=()):
    pal = bytearray(64)
    for i, v in enumerate(palette):
        struct.pack_into("<H", pal, 2 * i, v)
    v0 = bytearray(0x2000)
    v1 = bytearray(0x2000)
    for off, value in dict(bank0).items():
        v0[off] = value
    for off, value in dict(bank1).items():
        v1[off] = value
    return b"GBCBG\0" + bytes([lcdc, scx, scy, 0]) + bytes(pal) + bytes(v0) + bytes(v1)


def whole_map(index, attr=0x00, base=0x1800):
    """Bank-0 entries and bank-1 attribute bytes for the whole tile map."""
    return {base + i: index for i in range(1024)}, {base + i: attr for i in range(1024)}


def with_tile(bank, off, color):
    out = dict(bank)
    out.update({off + i: b for i, b in enumerate(tile(color))})
    return out


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        data = b"GBCBG\0" + bytes([0x91, 0, 0, 0]) + bytes(64) + bytes(0x4000)
        out = parse(data)
        self.assertEqual(out["first_line"], [[BLACK, 160]])
        self.assertTrue(out["complete"])

    def test_not_a_snapshot(self):
        with self.assertRaises(BgError):
            parse(b"GBXXXX" + bytes(100))

    def test_incomplete_snapshot(self):
        data = b"GBCBG\0" + bytes([0x91, 0, 0, 0]) + bytes(64) + bytes(0x2000)
        out = parse(data)
        self.assertFalse(out["complete"])
        self.assertIsNone(out["first_line"])
        self.assertIsNone(out["palette_pixels"])


class ColourTests(unittest.TestCase):
    def test_palette_is_bgr555(self):
        out = parse(snapshot(palette=[0x7C00, 0x001F, 0x03E0]))
        self.assertEqual(out["palettes"][:3], ["#0000ff", "#ff0000", "#00ff00"])

    def test_palette_entry_used(self):
        m, a = whole_map(0)
        out = parse(snapshot(palette=[0x001F], bank0=m, bank1=a))
        self.assertEqual(out["first_line"], [[RED, 160]])
        self.assertEqual(out["colors_used"], 1)


class MapTests(unittest.TestCase):
    def test_background_map_select(self):
        for lcdc, base in ((0x91, 0x1800), (0x99, 0x1C00)):
            m, a = whole_map(1, base=base)
            out = parse(snapshot(lcdc=lcdc, palette=[0x0000, 0x001F],
                                 bank0=with_tile(m, 0x0010, 1), bank1=a))
            self.assertEqual(out["first_line"], [[RED, 160]], hex(lcdc))
        # Tile map at $9800 must be ignored when LCDC selects $9C00.
        m, a = whole_map(1)
        out = parse(snapshot(lcdc=0x99, palette=[0x0000, 0x001F],
                             bank0=with_tile(m, 0x0010, 1), bank1=a))
        self.assertEqual(out["first_line"], [[BLACK, 160]])

    def test_tile_addressing_8000(self):
        m, a = whole_map(0x80)
        out = parse(snapshot(lcdc=0x91, palette=[0x0000, 0x001F],
                             bank0=with_tile(m, 0x0800, 1), bank1=a))
        self.assertEqual(out["first_line"], [[RED, 160]])

    def test_tile_addressing_8800(self):
        for index, off in ((0, 0x1000), (5, 0x1050), (127, 0x17F0),
                           (128, 0x0800), (255, 0x0FF0)):
            m, a = whole_map(index)
            out = parse(snapshot(lcdc=0x81, palette=[0x0000, 0x001F],
                                 bank0=with_tile(m, off, 1), bank1=a))
            self.assertEqual(out["first_line"], [[RED, 160]], index)
        # With signed addressing tile 0 lives at $9000, never $8000.
        m, a = whole_map(0)
        out = parse(snapshot(lcdc=0x81, palette=[0x0000, 0x001F],
                             bank0=with_tile(m, 0x0000, 1), bank1=a))
        self.assertEqual(out["first_line"], [[BLACK, 160]])


class AttributeTests(unittest.TestCase):
    def test_palette_select(self):
        m, a = whole_map(0, attr=0x07)
        palette = [0x0000] * 28 + [0x0000, 0x001F]
        out = parse(snapshot(palette=palette, bank0=with_tile(m, 0x0000, 1), bank1=a))
        self.assertEqual(out["first_line"], [[RED, 160]])
        self.assertEqual(out["palette_pixels"], [0] * 7 + [160 * 144])

    def test_tile_data_bank(self):
        m, a = whole_map(0, attr=0x08)
        out = parse(snapshot(palette=[0x0000, 0x001F],
                             bank0=with_tile(m, 0x0000, 0),
                             bank1=with_tile(a, 0x0000, 1)))
        self.assertEqual(out["first_line"], [[RED, 160]])
        a_clear = dict(a)
        a_clear[0x1800] = 0x00                           # bit 3 clear
        out = parse(snapshot(palette=[0x0000, 0x001F],
                             bank0=with_tile(m, 0x0000, 0),
                             bank1=with_tile(a_clear, 0x0000, 1)))
        self.assertEqual(out["first_line"], [[BLACK, 8], [RED, 152]])

    def test_horizontal_flip(self):
        # Tile 0 has one colour-1 pixel at its top-left; the rest of the
        # screen uses a solid black tile 1. Flipping moves that pixel to col 7.
        m, a = whole_map(1, attr=0x20)
        m[0x1800] = 0
        bank0 = with_tile(with_tile(m, 0x0000, 0), 0x0010, 0)
        bank0[0x0000] = 0x80
        out = parse(snapshot(palette=[0x0000, 0x001F], bank0=bank0, bank1=a))
        self.assertEqual(out["first_line"], [[BLACK, 7], [RED, 1], [BLACK, 152]])

    def test_vertical_flip(self):
        # Tile 0 sits in the map's bottom row (screen lines 136-143) and has
        # one colour-1 pixel at its top-left. Flipped, that pixel lands on the
        # last screen line, column 0.
        m, a = whole_map(1, attr=0x40)
        m[0x1800 + 17 * 32] = 0
        bank0 = with_tile(with_tile(m, 0x0000, 0), 0x0010, 0)
        bank0[0x0000] = 0x80
        out = parse(snapshot(palette=[0x0000, 0x001F], bank0=bank0, bank1=a))
        self.assertEqual(out["first_line"], [[BLACK, 160]])
        self.assertEqual(out["last_line"], [[RED, 1], [BLACK, 159]])

    def test_both_flips(self):
        m, a = whole_map(1, attr=0x60)
        m[0x1800 + 17 * 32] = 0
        bank0 = with_tile(with_tile(m, 0x0000, 0), 0x0010, 0)
        bank0[0x0000] = 0x80
        out = parse(snapshot(palette=[0x0000, 0x001F], bank0=bank0, bank1=a))
        self.assertEqual(out["last_line"], [[BLACK, 7], [RED, 1], [BLACK, 152]])


class ScrollTests(unittest.TestCase):
    def test_scroll(self):
        m, a = whole_map(1)
        for scx, scy in ((0, 0), (200, 0), (0, 200), (255, 255), (100, 100)):
            out = parse(snapshot(scx=scx, scy=scy, palette=[0x0000, 0x001F],
                                 bank0=with_tile(m, 0x0010, 1), bank1=a))
            self.assertEqual(out["first_line"], [[RED, 160]], (scx, scy))
            self.assertEqual(out["last_line"], [[RED, 160]], (scx, scy))

    def test_scroll_wraps_at_256_pixels(self):
        # Tile map column 0 is black and the rest is red; tile 1 is red on
        # every row. With SCX 255 the last screen column wraps to column 0,
        # and with SCY 255 the bottom line wraps to the map's top row.
        m = {0x1800 + i: 1 for i in range(1024)}
        for i in range(0x1800, 0x1C00, 32):
            m[i] = 0
        bank0 = with_tile(with_tile(m, 0x0000, 0), 0x0010, 1)
        out = parse(snapshot(scx=0, palette=[0x0000, 0x001F], bank0=bank0))
        self.assertEqual(out["first_line"], [[BLACK, 8], [RED, 152]])
        out = parse(snapshot(scx=255, palette=[0x0000, 0x001F], bank0=bank0))
        self.assertEqual(out["first_line"], [[RED, 1], [BLACK, 8], [RED, 151]])
        out = parse(snapshot(scy=255, palette=[0x0000, 0x001F], bank0=bank0))
        self.assertEqual(out["first_line"], [[BLACK, 8], [RED, 152]])
        self.assertEqual(out["last_line"], [[BLACK, 8], [RED, 152]])


if __name__ == "__main__":
    unittest.main()
