import unittest

from orickit.hires import HiresError, parse


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        out = parse(bytes([0x40]) * 8000)
        self.assertEqual(out["histogram"][0], 48000)
        self.assertTrue(out["complete"])

    def test_not_a_screen(self):
        with self.assertRaises(HiresError):
            parse(b"")
