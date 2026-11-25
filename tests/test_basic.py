import unittest

from yjkkit.vram import VramError, parse


class BasicTests(unittest.TestCase):
    def test_white_screen8(self):
        out = parse(b"V9958\x08" + bytes(32) + b"\xff" * (256 * 212))
        self.assertEqual(out["top_colors"], [["#ffffff", 256 * 212]])

    def test_not_a_dump(self):
        with self.assertRaises(VramError):
            parse(b"V9999\x08" + bytes(40))
