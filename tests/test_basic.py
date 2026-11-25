import struct
import unittest

from ktxkit.texture import KtxError, parse

ID = b"\xabKTX 11\xbb\r\n\x1a\n"


def rgba_2x2(pixels):
    head = ID + struct.pack("<13I", 0x04030201, 0x1401, 1, 0x1908, 0x1908, 0x1908, 2, 2, 0, 0, 1, 1, 0)
    return head + struct.pack("<I", 16) + bytes(pixels)


class BasicTests(unittest.TestCase):
    def test_single_rgba_level(self):
        out = parse(rgba_2x2(range(16)))
        self.assertEqual(out["levels"], [[2, 2, 16]])
        self.assertEqual(out["first_texels"], ["#00010203"])
        self.assertEqual(out["last_texel"], "#0c0d0e0f")

    def test_not_ktx(self):
        with self.assertRaises(KtxError):
            parse(b"\x00" * 80)
