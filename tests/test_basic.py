import unittest

from gbcbg.screen import BgError, parse


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        data = b"GBCBG\0" + bytes([0x91, 0, 0, 0]) + bytes(64) + bytes(0x4000)
        out = parse(data)
        self.assertEqual(out["first_line"], [["#000000", 160]])
        self.assertTrue(out["complete"])

    def test_not_a_snapshot(self):
        with self.assertRaises(BgError):
            parse(b"GBXXXX" + bytes(100))
