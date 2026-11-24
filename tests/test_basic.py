import struct
import unittest

from anikit.ani import parse


def minimal():
    anih = b"anih" + struct.pack("<I", 36) + struct.pack("<9I", 36, 0, 0, 0, 0, 0, 0, 6, 1)
    return b"RIFF" + struct.pack("<I", 4 + len(anih)) + b"ACON" + anih


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(minimal())
        self.assertEqual((out["frames"], out["default_rate"], out["icon_frames"]), (0, 6, True))

    def test_no_frames(self):
        self.assertEqual(parse(minimal())["frame_images"], [])
