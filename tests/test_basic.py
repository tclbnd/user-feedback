import unittest

from x68kspr.scene import SceneError, parse


class BasicTests(unittest.TestCase):
    def test_display_off(self):
        data = b"X68SPBG\0" + bytes(1024 + 12 + 512 + 0x8000)
        out = parse(data)
        self.assertFalse(out["display"])
        self.assertEqual(out["first_row"], [["#000000", 256]])

    def test_not_a_snapshot(self):
        with self.assertRaises(SceneError):
            parse(b"X68TEXT\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
