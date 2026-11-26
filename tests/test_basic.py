import unittest

from maria.frame import MariaError, parse


class BasicTests(unittest.TestCase):
    def test_empty_frame(self):
        regs = bytearray(32)
        regs[0] = 0x1A
        out = parse(b"A7800MS\0" + bytes(regs) + bytes(0x10000))
        self.assertEqual(out["first_row"], [["1a", 160]])
        self.assertEqual(out["background_pixels"], 243 * 160)

    def test_not_a_snapshot(self):
        with self.assertRaises(MariaError):
            parse(b"A2600MS\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
