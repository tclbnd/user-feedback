import struct
import unittest

from cmfkit.cmf import parse


def minimal():
    h = bytearray(0x28)
    h[:4] = b"CTMF"
    h[4], h[5] = 1, 1
    struct.pack_into("<7H", h, 6, 0x28, 0x28, 96, 96, 0, 0, 0)
    struct.pack_into("<HH", h, 0x24, 0, 120)
    return bytes(h) + b"\x00\xff\x2f\x00"


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(minimal())
        self.assertEqual((out["version"], out["ticks_per_quarter"], out["end_of_track"]), ("1.1", 96, True))

    def test_empty(self):
        self.assertEqual(parse(minimal())["notes"], 0)
