import struct
import unittest

from amiscr.screen import ScreenError, parse


class BasicTests(unittest.TestCase):
    def test_one_plane_screen(self):
        regs = [0x000, 0xFFF] + [0] * 30
        data = b"AMISCR" + struct.pack(">HHHH", 0x1200, 0, 16, 1) + struct.pack(">32H", *regs) + b"\xff\x00"
        out = parse(data)
        self.assertEqual(out["first_line"], [["#ffffff", 8], ["#000000", 8]])

    def test_not_a_dump(self):
        with self.assertRaises(ScreenError):
            parse(b"AMIXXX" + bytes(100))
