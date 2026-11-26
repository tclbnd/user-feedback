import unittest

from brrkit.brr import BrrError, parse


class BasicTests(unittest.TestCase):
    def test_silent_block(self):
        out = parse(bytes(9))
        self.assertEqual(out["blocks"], 1)
        self.assertEqual(out["peak"], "0.000000")

    def test_not_brr(self):
        with self.assertRaises(BrrError):
            parse(bytes(10))
