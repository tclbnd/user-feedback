import unittest

from gime.screen import GimeError, parse


class BasicTests(unittest.TestCase):
    def test_blank_two_colour_screen(self):
        regs = bytes([0x80, 0x00, 0, 0, 0xC0, 0x00, 0, 0])
        out = parse(b"GIME3SN\0" + regs + bytes(16) + bytes(0x80000))
        self.assertEqual(out["colors"], 2)
        self.assertEqual(out["first_row"], [["#000000", 128]])

    def test_not_a_snapshot(self):
        with self.assertRaises(GimeError):
            parse(b"GIME2SN\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
