import struct
import unittest

from tilekit.tiles import TileError, parse, pixel


def tiles(platform, tiles=(), entries=0, palette=b""):
    head = b"TILE" + platform.encode("ascii").ljust(4) + struct.pack("<HH", len(tiles), entries)
    return head + palette + b"".join(tiles)


class BasicTests(unittest.TestCase):
    def test_nes_tile(self):
        t = bytes([0x80] + [0] * 7 + [0x80] + [0] * 7)
        out = parse(b"TILE" + b"NES " + struct.pack("<HH", 1, 0) + t)
        self.assertEqual(out["first_tile"][0], "30000000")

    def test_not_tiles(self):
        with self.assertRaises(TileError):
            parse(b"TILX" + bytes(20))

    def test_unknown_platform(self):
        with self.assertRaises(TileError):
            parse(tiles("ZX80"))


class GBTests(unittest.TestCase):
    def test_interleaved_rows(self):
        tile = bytes([0x80, 0x80] + [0] * 14)       # value 3 at (0, 0)
        out = parse(tiles("GB", [tile], entries=4, palette=bytes([0xE4])))
        self.assertEqual(out["bpp"], 2)
        self.assertEqual(out["complete"], True)
        self.assertEqual(out["first_tile"][0], "30000000")

    def test_bgp_register_shades(self):
        out = parse(tiles("GB", [bytes(16)], entries=4, palette=bytes([0xE4])))
        self.assertEqual(out["palette"], ["#ffffff", "#aaaaaa", "#555555", "#000000"])
        out = parse(tiles("GB", [bytes(16)], entries=4, palette=bytes([0x1B])))
        self.assertEqual(out["palette"], ["#000000", "#555555", "#aaaaaa", "#ffffff"])

    def test_bgp_byte_present_without_entry_count(self):
        # The BGP register is one byte whatever the header's entry count says,
        # and tile data starts right after it.
        out = parse(tiles("GB", [bytes(16)], entries=0, palette=bytes([0x1B])))
        self.assertEqual(out["palette"], ["#000000", "#555555", "#aaaaaa", "#ffffff"])
        self.assertEqual(out["first_tile"][0], "00000000")


class PlanarTests(unittest.TestCase):
    def test_snes_paired_planes(self):
        tile = bytes([0x80, 0x80] + [0] * 14 + [0x80, 0x80] + [0] * 14)
        self.assertEqual(pixel("SNES", tile, 0, 0), 15)
        self.assertEqual(pixel("SNES", bytes([0x80] + [0] * 31), 0, 0), 1)

    def test_md_sequential_planes(self):
        tile = bytes([0x80] + [0] * 23 + [0x80] + [0] * 7)   # planes 0 and 3
        self.assertEqual(pixel("MD", tile, 0, 0), 9)

    def test_pce_paired_planes(self):
        tile = bytes([0, 0x80] + [0] * 30)
        self.assertEqual(pixel("PCE", tile, 0, 0), 2)


class PackedTests(unittest.TestCase):
    def test_gba_low_nibble_first(self):
        self.assertEqual(pixel("GBA", bytes([0x12] + [0] * 31), 0, 0), 2)
        self.assertEqual(pixel("GBA", bytes([0x12] + [0] * 31), 1, 0), 1)

    def test_sms_high_nibble_first(self):
        self.assertEqual(pixel("SMS", bytes([0x12] + [0] * 31), 0, 0), 1)
        self.assertEqual(pixel("SMS", bytes([0x12] + [0] * 31), 1, 0), 2)


class PaletteTests(unittest.TestCase):
    def test_words_widen_to_white(self):
        words = {"SNES": 0x7FFF, "GBA": 0x7FFF, "MD": 0x0EEE,
                 "GG": 0x0FFF, "PCE": 0x01FF}
        for platform, word in words.items():
            out = parse(tiles(platform, [bytes(32)], entries=1,
                              palette=struct.pack("<H", word)))
            self.assertEqual(out["palette"], ["#ffffff"], platform)

    def test_snes_green(self):
        out = parse(tiles("SNES", [bytes(32)], entries=1, palette=struct.pack("<H", 0x03E0)))
        self.assertEqual(out["palette"], ["#00ff00"])

    def test_sms_bytes(self):
        out = parse(tiles("SMS", [bytes(32)], entries=2, palette=bytes([0x00, 0x3F])))
        self.assertEqual(out["palette"], ["#000000", "#ffffff"])

    def test_nes_has_no_palette(self):
        self.assertIsNone(parse(tiles("NES", [bytes(16)], entries=8, palette=bytes(8)))["palette"])


class ContainerTests(unittest.TestCase):
    def test_truncated_is_incomplete(self):
        # Header claims 3 tiles but only one tile's bytes follow.
        out = parse(b"TILEGBA " + struct.pack("<HH", 3, 0) + bytes(32))
        self.assertFalse(out["complete"])
        self.assertEqual(out["tiles"], 3)
        self.assertEqual(out["histogram"], [64] + [0] * 15)

    def test_empty_set(self):
        out = parse(tiles("PCE"))
        self.assertTrue(out["complete"])
        self.assertIsNone(out["first_tile"])
        self.assertIsNone(out["last_row"])

    def test_last_row(self):
        tile = bytes([0] * 7 + [0x80] + [0] * 24)        # colour 1 at (0, 7)
        out = parse(tiles("MD", [tile]))
        self.assertEqual(out["last_row"], "10000000")
        self.assertEqual(out["colors_used"], 2)

