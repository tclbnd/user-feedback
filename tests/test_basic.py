import unittest

from pcevdc.screen import VdcError, parse


class BasicTests(unittest.TestCase):
    def test_empty_screen(self):
        data = b"PCEVDC\0\0" + bytes(8) + bytes(0x10000 + 512 + 1024)
        out = parse(data)
        self.assertEqual(out["first_row"], [["#000000", 256]])
        self.assertEqual(out["sprites_shown"], 0)

    def test_not_a_snapshot(self):
        with self.assertRaises(VdcError):
            parse(b"PCEVCE\0\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
