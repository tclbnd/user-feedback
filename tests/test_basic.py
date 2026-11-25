import struct
import unittest

from artkit.art import ArtError, parse


class BasicTests(unittest.TestCase):
    def test_empty_slot(self):
        data = struct.pack("<4I", 1, 0, 0, 0) + struct.pack("<hhI", 0, 0, 0)
        out = parse(data)
        self.assertEqual((out["nonempty"], out["pixel_bytes"]), (0, 0))

    def test_not_art(self):
        with self.assertRaises(ArtError):
            parse(struct.pack("<4I", 2, 0, 0, 0))
