import unittest

from okikit.rom import RomError, parse


class BasicTests(unittest.TestCase):
    def test_empty_table(self):
        out = parse(bytes(0x1000))
        self.assertEqual(out["phrases"], 0)

    def test_too_small(self):
        with self.assertRaises(RomError):
            parse(bytes(100))
