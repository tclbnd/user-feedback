import unittest

from snesbg.layer import LayerError, parse


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        data = b"SNESBG\0\0" + bytes([1, 0, 0, 0]) + bytes(4) + bytes(512) + bytes(0x10000)
        out = parse(data)
        self.assertEqual(out["first_row"], [["#000000", 256]])
        self.assertEqual(out["bpp"], 4)

    def test_not_a_snapshot(self):
        with self.assertRaises(LayerError):
            parse(b"SNESOBJ\0" + bytes(600))


if __name__ == "__main__":
    unittest.main()
