import struct
import unittest

from gf1pat.patch import PatchError, parse


def one_sample():
    head = b"GF1PATCH110\0ID#000002\0" + b"demo".ljust(60, b"\0") + struct.pack("<BBBHHI", 1, 14, 0, 1, 127, 100) + bytes(36)
    inst = bytes(2) + b"Demo".ljust(16, b"\0") + struct.pack("<i", 0) + bytes([1]) + bytes(40)
    layer = bytes(2) + struct.pack("<i", 0) + bytes([1]) + bytes(40)
    h = b"wave\0\0\0" + bytes(1) + struct.pack("<iiiHiiihB", 4, 0, 0, 8000, 0, 0, 0, 0, 7) + bytes(18) + bytes([0]) + struct.pack("<hH", 60, 1024) + bytes(36)
    return head + inst + layer + h + bytes([0, 10, 20, 30])


class BasicTests(unittest.TestCase):
    def test_single_sample(self):
        out = parse(one_sample())
        self.assertEqual(out["description"], "demo")
        self.assertEqual(out["samples"][0]["frames"], 4)
        self.assertEqual(out["samples"][0]["name"], "wave")

    def test_not_a_patch(self):
        with self.assertRaises(PatchError):
            parse(b"GF2PATCH110\0" + bytes(300))


if __name__ == "__main__":
    unittest.main()
