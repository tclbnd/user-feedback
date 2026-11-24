import struct
import unittest

from s98kit.s98 import parse

LOG = b"S983" + struct.pack("<6I", 0, 0, 0, 0, 0x30, 0) + struct.pack("<I", 1) + struct.pack("<4I", 4, 7987200, 0, 0) + b"\xfd"


class BasicTests(unittest.TestCase):
    def test_device(self):
        out = parse(LOG)
        self.assertEqual((out["version"], out["devices"][0]["type"], out["end_marker"]), ("3", "ym2608", True))

    def test_empty(self):
        self.assertEqual(parse(LOG)["syncs"], 0)
