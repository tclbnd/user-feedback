import unittest

from c93kit.c93 import C93Error, parse


def pack(frames):
    return b"C93VIDEO" + len(frames).to_bytes(2, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_intra_frame(self):
        body = bytes([0x02]) + (bytes([0xFF]) + bytes(128)) * 480
        out = parse(pack([body]))
        self.assertEqual(out["frames"][0]["ok"], True)
        self.assertEqual(out["frames"][0]["key"], True)

    def test_bad_magic(self):
        with self.assertRaises(C93Error):
            parse(b"C93AUDIO" + bytes(4))


if __name__ == "__main__":
    unittest.main()
