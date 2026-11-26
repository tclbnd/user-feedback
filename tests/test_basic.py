import unittest

from ngspr.sprites import SpriteError, parse


class BasicTests(unittest.TestCase):
    def test_no_sprites(self):
        data = b"NGSPR\0\0\0" + bytes(2 * 0x8800) + bytes(8192) + bytes(128)
        out = parse(data)
        self.assertEqual(out["sprites"], 0)
        self.assertEqual(out["first_row"], [["#000000", 320]])

    def test_not_a_snapshot(self):
        with self.assertRaises(SpriteError):
            parse(b"NGFIX\0\0\0" + bytes(100))


if __name__ == "__main__":
    unittest.main()
