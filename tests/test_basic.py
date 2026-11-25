import struct
import unittest

from tilekit.tiles import TileError, parse


class BasicTests(unittest.TestCase):
    def test_nes_tile(self):
        t = bytes([0x80] + [0] * 7 + [0x80] + [0] * 7)
        out = parse(b"TILE" + b"NES " + struct.pack("<HH", 1, 0) + t)
        self.assertEqual(out["first_tile"][0], "30000000")

    def test_not_tiles(self):
        with self.assertRaises(TileError):
            parse(b"TILX" + bytes(20))
