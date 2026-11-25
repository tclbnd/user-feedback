import unittest

from qlkit.screen import ScreenError, parse


class BasicTests(unittest.TestCase):
    def test_white_mode4_screen(self):
        out = parse(b"QLSCR\x04" + b"\xff" * 32768)
        self.assertEqual(out["histogram"][7], 512 * 256)

    def test_not_a_dump(self):
        with self.assertRaises(ScreenError):
            parse(b"QLXXX\x04")
