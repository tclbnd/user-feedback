import struct
import unittest

from chanf.vram import ChanFError, parse


class BasicTests(unittest.TestCase):
    def test_empty_log(self):
        out = parse(b"CHFLOG\0\0" + struct.pack("<BxxxI", 0, 0))
        self.assertEqual(out["first_row"], [["#101010", 128]])
        self.assertEqual(out["row_palettes"], [64, 0, 0, 0])

    def test_not_a_log(self):
        with self.assertRaises(ChanFError):
            parse(b"CHFLOX\0\0" + bytes(10))


if __name__ == "__main__":
    unittest.main()
