import struct
import unittest

from icckit.icc import IccError, parse


def bare():
    head = bytearray(132)
    struct.pack_into(">I", head, 0, 132)
    head[12:24] = b"mntrRGB XYZ "
    head[36:40] = b"acsp"
    return bytes(head)


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(bare())
        self.assertEqual((out["class"], out["color_space"], out["tags"]), ("display", "RGB", 0))

    def test_not_icc(self):
        with self.assertRaises(IccError):
            parse(bytes(200))
