import struct
import unittest

from wadkit.wad import WadError, parse


def wad2(lumps):
    body, entries, pos = b"", b"", 12
    for name, typ, data in lumps:
        entries += struct.pack("<3iBBH", pos, len(data), len(data), typ, 0, 0) + name.encode().ljust(16, b"\0")
        body += data
        pos += len(data)
    return b"WAD2" + struct.pack("<2i", len(lumps), 12 + len(body)) + body + entries


class BasicTests(unittest.TestCase):
    def test_quake_picture(self):
        pal = bytes(range(3)) + bytes(765)
        pic = struct.pack("<2I", 2, 1) + bytes([0, 0])
        out = parse(wad2([("PALETTE", 0x40, pal), ("CONBACK", 0x42, pic)]))
        self.assertEqual(out["lumps"][1]["first_pixel"], "#000102")

    def test_not_a_wad(self):
        with self.assertRaises(WadError):
            parse(b"PACK" + bytes(20))
