import struct
import unittest

from aykit.ay import parse

HEAD = b"ZXAYEMUL" + bytes([3, 1]) + b"\0\0\0\0\0\0" + bytes([0, 0]) + b"\0\0"


class BasicTests(unittest.TestCase):
    def test_versions(self):
        out = parse(HEAD)
        self.assertEqual((out["file_version"], out["player_version"], out["first_song"]), (3, 1, 0))

    def test_not_ay(self):
        with self.assertRaises(ValueError):
            parse(b"ZXAYxxxx" + bytes(20))
