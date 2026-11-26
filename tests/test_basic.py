import unittest

from mzvid.screen import MzError, parse


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        out = parse(b"MZ700VR\0" + bytes(1000) + bytes(1000) + bytes(4096))
        self.assertEqual(out["first_row"], [["#000000", 320]])
        self.assertEqual(out["height"], 200)

    def test_not_a_snapshot(self):
        with self.assertRaises(MzError):
            parse(b"MZ700XX\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
