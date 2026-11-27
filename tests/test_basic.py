import unittest

from vidkit.bethsoft import VidError, parse


def header(n, w, h, delay):
    return b"VID\0\0" + b"".join(v.to_bytes(2, "little") for v in (n, w, h, delay, 14))


class BasicTests(unittest.TestCase):
    def test_one_key_frame(self):
        data = header(1, 4, 1, 0) + bytes([0x03, 1, 0, 4, 9, 9, 9, 9, 0, 0x14])
        out = parse(data)
        self.assertEqual(out["ended"], "eof")
        self.assertEqual(len(out["frames"]), 1)

    def test_not_vid(self):
        with self.assertRaises(VidError):
            parse(b"AVI" + bytes(20))


if __name__ == "__main__":
    unittest.main()
