import struct
import unittest
import zlib

from ttakit.tta import TtaError, parse


def header(rate=44100, samples=0):
    head = b"TTA1" + struct.pack("<HHHII", 1, 2, 16, rate, samples)
    return head + struct.pack("<I", zlib.crc32(head)) + struct.pack("<I", zlib.crc32(b""))


class BasicTests(unittest.TestCase):
    def test_empty(self):
        out = parse(header())
        self.assertEqual((out["channels"], out["bits"], out["frames"]), (2, 16, 0))

    def test_not_tta(self):
        with self.assertRaises(TtaError):
            parse(b"TTA2" + bytes(30))
