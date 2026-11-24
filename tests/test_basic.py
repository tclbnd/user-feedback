import unittest

from spukit.spu import SpuError, parse

BLANK = bytes(51104)


class BasicTests(unittest.TestCase):
    def test_size(self):
        self.assertEqual(parse(BLANK)["bytes"], 51104)

    def test_wrong_size(self):
        with self.assertRaises(SpuError):
            parse(bytes(32034))
