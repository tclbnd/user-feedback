import unittest

from pc98kit.gvram import GvError, parse


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        data = b"PC98GV\0\0" + bytes([1, 0, 0, 0]) + bytes(4) + (400).to_bytes(2, "little") + bytes(6) + bytes(48) + bytes(4 * 0x8000)
        out = parse(data)
        self.assertEqual(out["first_row"], [["#000000", 640]])
        self.assertTrue(out["complete"])

    def test_not_a_dump(self):
        with self.assertRaises(GvError):
            parse(b"PC88GV\0\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
