import struct
import unittest
import zlib

from cartkit.cart import CartError, parse


def image(byte):
    px = bytes([0xFC | (byte >> 4 & 3), 0xFC | (byte >> 2 & 3), 0xFC | (byte & 3), 0xFC | byte >> 6])
    raw = b"".join(b"\x00" + px * 160 for _ in range(205))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 160, 205, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class BasicTests(unittest.TestCase):
    def test_filled_cart(self):
        out = parse(image(0x11))
        self.assertEqual(out["gfx_histogram"][1], 128 * 128)
        self.assertEqual(out["version"], 0x11)

    def test_not_a_cart(self):
        with self.assertRaises(CartError):
            parse(b"GIF89a")
