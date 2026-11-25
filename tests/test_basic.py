import unittest

from samkit.screen import ScreenError, parse


class BasicTests(unittest.TestCase):
    def test_black_mode4_screen(self):
        out = parse(b"SAMSCR\x04" + bytes(16) + bytes(24576))
        self.assertEqual(out["colors"], {"#000000": 256 * 192})

    def test_not_a_dump(self):
        with self.assertRaises(ScreenError):
            parse(b"SAMXXX\x04" + bytes(40))
