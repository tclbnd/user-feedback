import struct
import unittest

from antickit.dlist import DisplayListError, parse


def snap(dl, at=0x3000):
    mem = bytearray(65536)
    mem[at:at + len(dl)] = dl
    return b"XLMEM\x00" + struct.pack("<HB", at, 0x22) + bytes(mem)


class BasicTests(unittest.TestCase):
    def test_text_screen(self):
        dl = b"\x70\x70\x70\x42\x00\x40" + b"\x02" * 23 + b"\x41\x00\x30"
        out = parse(snap(dl))
        self.assertEqual(out["scanlines"], 24 + 24 * 8)
        self.assertEqual(out["fetch"], ["4000-43c0"])

    def test_not_a_snapshot(self):
        with self.assertRaises(DisplayListError):
            parse(b"XLMEM\x00" + bytes(10))
