import unittest

from ymzkit.voice import YmzError, parse


class BasicTests(unittest.TestCase):
    def test_no_voices(self):
        out = parse(b"YMZ280B\0" + bytes(256) + bytes(4))
        self.assertEqual(out["voices"], [])
        self.assertEqual(out["rom_bytes"], 0)

    def test_not_a_snapshot(self):
        with self.assertRaises(YmzError):
            parse(b"YMZ280X\0" + bytes(260))


if __name__ == "__main__":
    unittest.main()
