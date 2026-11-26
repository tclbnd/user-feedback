import unittest

from mo5scr.screen import Mo5Error, parse


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        out = parse(b"MO5SCRN\0" + bytes(8) + bytes(16384))
        self.assertEqual(out["first_row"], [["0", 320]])
        self.assertEqual(out["border"], "0")

    def test_not_a_snapshot(self):
        with self.assertRaises(Mo5Error):
            parse(b"MO5SCRX\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
