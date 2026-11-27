import unittest
import zlib

from zmbvkit.zmbv import ZmbvError, parse


def pak(w, h, frames):
    return b"ZMBVPAK\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + len(frames).to_bytes(2, "little") \
        + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_key_frame_16bit(self):
        pic = bytes(range(32))
        frame = bytes([1, 0, 1, 1, 6, 4, 4]) + zlib.compress(pic)
        out = parse(pak(4, 4, [frame]))
        self.assertEqual(out["bits"], 16)
        self.assertEqual(out["frames"][0]["crc32"], "%08x" % zlib.crc32(pic))

    def test_bad_magic(self):
        with self.assertRaises(ZmbvError):
            parse(b"ZMBVXXX\0" + bytes(6))


if __name__ == "__main__":
    unittest.main()
