import struct
import unittest

from spritekit.area import AreaError, parse


class BasicTests(unittest.TestCase):
    def test_header_count(self):
        out = parse(struct.pack("<3I", 0, 16, 16))
        self.assertEqual(out["sprites_declared"], 0)
        self.assertEqual(out["sprites"], [])

    def test_not_a_sprite_file(self):
        with self.assertRaises(AreaError):
            parse(b"\x01\x00")
