import struct
import unittest

from icokit.ico import IcoError, parse


def icon_8bit(w, h):
    hdr = struct.pack("<IiiHHIIiiII", 40, w, 2 * h, 1, 8, 0, 0, 0, 0, 0, 0)
    img = hdr + bytes(1024) + bytes(w * h) + bytes(((w + 31) // 32) * 4 * h)
    return struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", w, h, 0, 0, 1, 8, len(img), 22) + img


class BasicTests(unittest.TestCase):
    def test_single_icon(self):
        out = parse(icon_8bit(32, 32))
        self.assertEqual(out["type"], "icon")
        self.assertEqual(out["images"][0]["bits"], 8)

    def test_not_an_icon(self):
        with self.assertRaises(IcoError):
            parse(b"\x00\x00\x05\x00\x01\x00" + bytes(40))


if __name__ == "__main__":
    unittest.main()
