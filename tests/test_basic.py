import struct
import unittest

from wmfkit.wmf import WmfError, parse


def empty():
    eof = struct.pack("<IH", 3, 0)
    return struct.pack("<HHHIHIH", 1, 9, 0x0300, 12, 0, 3, 0) + eof


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(empty())
        self.assertEqual((out["version"], out["placeable"], out["objects"]), ("3.0", False, 0))

    def test_not_metafile(self):
        with self.assertRaises(WmfError):
            parse(bytes(10))
