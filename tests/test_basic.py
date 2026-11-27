import struct
import unittest

from m7kit.frame import M7Error, parse


class BasicTests(unittest.TestCase):
    def test_empty_vram(self):
        data = b"SNESM7\0\0" + struct.pack("<B4H4H", 0, 0x100, 0, 0, 0x100, 0, 0, 0, 0) + struct.pack("<I", 0)
        out = parse(data)
        self.assertEqual(out["opaque"], 0)
        self.assertEqual(out["matrix"][0], "1.0000")

    def test_not_a_snapshot(self):
        with self.assertRaises(M7Error):
            parse(b"SNESM8\0\0" + bytes(21))


if __name__ == "__main__":
    unittest.main()
