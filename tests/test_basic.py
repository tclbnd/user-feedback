import struct
import unittest

from timkit.tim import TimError, parse


class BasicTests(unittest.TestCase):
    def test_grey_16bit_texel(self):
        word = 16 << 10 | 16 << 5 | 16
        data = struct.pack("<II", 0x10, 2) + struct.pack("<I4H", 14, 0, 0, 1, 1) + struct.pack("<H", word)
        out = parse(data)
        self.assertEqual(out["first_row"], ["#848484"])
        self.assertTrue(out["complete"])

    def test_not_a_tim(self):
        with self.assertRaises(TimError):
            parse(b"\x11\x00\x00\x00" + bytes(20))
