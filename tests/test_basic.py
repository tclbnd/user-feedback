import struct
import unittest

from xcfkit.xcf import XcfError, parse


def empty_canvas():
    head = b"gimp xcf file\0" + struct.pack(">III", 32, 16, 0)
    props = struct.pack(">II", 17, 1) + b"\0" + struct.pack(">II", 0, 0)
    return head + props + struct.pack(">II", 0, 0)


class BasicTests(unittest.TestCase):
    def test_no_layers(self):
        out = parse(empty_canvas())
        self.assertEqual((out["width"], out["height"]), (32, 16))
        self.assertEqual(out["layers"], [])
        self.assertEqual(out["compression"], "none")

    def test_not_xcf(self):
        with self.assertRaises(XcfError):
            parse(b"gimp xcd file\0" + bytes(40))


if __name__ == "__main__":
    unittest.main()
