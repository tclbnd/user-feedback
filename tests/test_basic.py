import struct
import unittest

from pvrtex.pvr import PvrError, parse


class BasicTests(unittest.TestCase):
    def test_flat_rgb565(self):
        body = struct.pack("<BBHHH", 1, 1, 0, 2, 2) + struct.pack("<4H", *[0xFFFF] * 4)
        out = parse(b"PVRT" + struct.pack("<I", len(body)) + body)
        self.assertEqual(out["first_row"], [["#ffffffff", 2]])
        self.assertEqual(out["data_format"], "twiddled")

    def test_not_a_texture(self):
        with self.assertRaises(PvrError):
            parse(b"PVRX" + bytes(40))


if __name__ == "__main__":
    unittest.main()
