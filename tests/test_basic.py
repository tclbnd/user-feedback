import unittest

from xakit.xa import XaError, parse


class BasicTests(unittest.TestCase):
    def test_silent_sector(self):
        data = bytes([1, 0, 0x64, 0]) * 2 + bytes(2328)
        out = parse(data)
        self.assertEqual(out["sectors"], 1)
        self.assertEqual(out["streams"][0]["peak"], "0.000000")

    def test_not_xa(self):
        with self.assertRaises(XaError):
            parse(b"\x00" * 100)
