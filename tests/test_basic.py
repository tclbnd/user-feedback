import unittest

from cramkit.cram import CramError, parse


def pak(w, h, depth, frames):
    return b"CRAMPAK\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([depth]) \
        + len(frames).to_bytes(2, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_single_fill(self):
        out = parse(pak(4, 4, 8, [bytes([7, 0x80])]))
        self.assertEqual(out["frames"][0]["fill"], 1)
        self.assertEqual(out["frames"][0]["crc32"], "%08x" % __import__("zlib").crc32(bytes([7] * 16)))

    def test_bad_depth(self):
        with self.assertRaises(CramError):
            parse(pak(4, 4, 24, []))


if __name__ == "__main__":
    unittest.main()
