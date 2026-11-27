import unittest

from pvgfx.screen import PvError, parse


class BasicTests(unittest.TestCase):
    def test_blank_screen(self):
        out = parse(b"PV1000VR" + b"\x00" + bytes(2048) + bytes(8 * 256 * 32))
        self.assertEqual(out["first_row"], [["#000000", 256]])
        self.assertTrue(out["display"])

    def test_not_a_snapshot(self):
        with self.assertRaises(PvError):
            parse(b"PV1000XX" + bytes(3000))


if __name__ == "__main__":
    unittest.main()
