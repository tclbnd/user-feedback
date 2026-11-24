import unittest

from koalakit.koala import parse

BLANK = bytes(10001)


class BasicTests(unittest.TestCase):
    def test_background(self):
        self.assertEqual(parse(BLANK)["background"], "black")

    def test_bad_size(self):
        with self.assertRaises(ValueError):
            parse(bytes(500))
