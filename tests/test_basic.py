import os
import struct
import unittest

from tilekit.tiles import TileError, parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def header(platform, count, entries):
    return b"TILE" + platform.encode("ascii").ljust(4) + struct.pack("<HH", count, entries)


class BasicTests(unittest.TestCase):
    def test_nes_tile(self):
        t = bytes([0x80] + [0] * 7 + [0x80] + [0] * 7)
        out = parse(header("NES", 1, 0) + t)
        self.assertEqual(out["platform"], "NES")
        self.assertEqual(out["bpp"], 2)
        self.assertEqual(out["first_tile"][0], "30000000")
        self.assertIsNone(out["palette"])
        self.assertEqual(out["histogram"][3], 1)
        self.assertEqual(out["histogram"][0], 63)
        self.assertEqual(out["colors_used"], 2)
        self.assertTrue(out["complete"])

    def test_not_tiles(self):
        with self.assertRaises(TileError):
            parse(b"TILX" + bytes(20))

    def test_unsupported_platform(self):
        with self.assertRaises(TileError):
            parse(header("N64", 0, 0))


class GameBoyTests(unittest.TestCase):
    def test_gb_tile_and_bgp(self):
        # Same 2bpp planar layout as NES; BGP 0xE4: shades 0,1,2,3 in order.
        t = bytes([0x80] + [0] * 7 + [0x80] + [0] * 7)
        out = parse(header("GB", 1, 1) + bytes([0xE4]) + t)
        self.assertEqual(out["platform"], "GB")
        self.assertEqual(out["bpp"], 2)
        self.assertEqual(out["first_tile"][0], "30000000")
        self.assertEqual(out["palette"],
                         ["#ffffff", "#aaaaaa", "#555555", "#000000"])
        self.assertEqual(len(out["histogram"]), 4)

    def test_gb_bgp_reordered(self):
        # BGP 0x00: every index maps to shade 0 (white).
        t = bytes(16)
        out = parse(header("GB", 1, 1) + bytes([0x00]) + t)
        self.assertEqual(out["palette"], ["#ffffff"] * 4)


class SNESTests(unittest.TestCase):
    def test_snes_planes_and_bgr555(self):
        # Planes 0 and 1 set the leftmost pixel -> index 3.
        tile = bytearray(32)
        tile[0] = 0x80   # plane 0, row 0
        tile[8] = 0x80   # plane 1, row 0
        pal = struct.pack("<HH", 0x03E0, 0x001F)  # pure green, pure blue (BGR)
        out = parse(header("SNES", 1, 2) + pal + bytes(tile))
        self.assertEqual(out["bpp"], 4)
        self.assertEqual(out["first_tile"][0], "30000000")
        self.assertEqual(out["palette"], ["#00ff00", "#0000ff"])
        self.assertEqual(len(out["histogram"]), 16)

    def test_snes_component_widening(self):
        # Red=1 in 5-bit space (bits 10-14) widens to round(255/31) = 8.
        out = parse(header("SNES", 0, 1) + struct.pack("<H", 0x0400))
        self.assertEqual(out["palette"], ["#080000"])

    def test_snes_example_file(self):
        with open(os.path.join(ROOT, "examples", "hero.tiles"), "rb") as fh:
            out = parse(fh.read())
        self.assertEqual(out["platform"], "SNES")
        self.assertEqual(out["tiles"], 3)
        self.assertTrue(out["complete"])
        self.assertEqual(len(out["palette"]), 16)
        self.assertEqual(out["palette"][0], "#00ff00")
        self.assertEqual(out["first_tile"][0], "8b8b4747")
        self.assertEqual(len(out["last_row"]), 8)


class MegaDriveTests(unittest.TestCase):
    def test_md_interleaved_planes_and_9bit_bgr(self):
        # Row 0 stored as [plane3, plane2, plane1, plane0]; plane0 bit7 -> idx 1.
        tile = bytearray(32)
        tile[3] = 0x80
        pal = struct.pack("<HH", 0x01C0, 0x0038)  # pure red, pure green (BGR)
        out = parse(header("MD", 1, 2) + pal + bytes(tile))
        self.assertEqual(out["bpp"], 4)
        self.assertEqual(out["first_tile"][0], "10000000")
        self.assertEqual(out["palette"], ["#ff0000", "#00ff00"])

    def test_md_component_widening(self):
        # Red=1 in 3-bit space (bits 6-8) widens to round(255/7) = 36 = 0x24.
        out = parse(header("MD", 0, 1) + struct.pack("<H", 0x0040))
        self.assertEqual(out["palette"], ["#240000"])


class GBATests(unittest.TestCase):
    def test_gba_packed_nibbles_and_bgr555(self):
        # Two pixels per byte, low nibble first: 0x01 -> pixel 0 = index 1.
        tile = bytes([0x01, 0, 0, 0]) + bytes(28)
        pal = struct.pack("<HH", 0x03E0, 0x001F)  # pure green, pure blue (BGR)
        out = parse(header("GBA", 1, 2) + pal + tile)
        self.assertEqual(out["bpp"], 4)
        self.assertEqual(out["first_tile"][0], "10000000")
        self.assertEqual(out["palette"], ["#00ff00", "#0000ff"])

    def test_gba_high_nibble_pixel(self):
        tile = bytes([0x10, 0, 0, 0]) + bytes(28)
        out = parse(header("GBA", 1, 0) + tile)
        self.assertEqual(out["first_tile"][0], "01000000")


class CompletenessTests(unittest.TestCase):
    def test_truncated_tiles(self):
        t = bytes(16)
        out = parse(header("NES", 2, 0) + t)  # only one of two tiles present
        self.assertEqual(out["tiles"], 2)
        self.assertFalse(out["complete"])
        self.assertEqual(out["first_tile"], ["00000000"] * 8)
        self.assertEqual(out["last_row"], "00000000")

    def test_no_tiles(self):
        out = parse(header("GBA", 0, 0))
        self.assertTrue(out["complete"])
        self.assertIsNone(out["first_tile"])
        self.assertIsNone(out["last_row"])
