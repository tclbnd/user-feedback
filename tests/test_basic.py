import unittest

from uvi.screen import UviError, parse


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        mem = bytearray(0x300)
        mem[0x1F8], mem[0x1F9] = 0x40, 0x80 | 0x06
        out = parse(b"ARCADIA\0" + bytes(mem) + bytes(0x1C0))
        self.assertEqual(out["first_line"], [["#0000ff", 128]])
        self.assertEqual(out["rows"], 26)

    def test_not_a_snapshot(self):
        with self.assertRaises(UviError):
            parse(b"ARCADIX\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
