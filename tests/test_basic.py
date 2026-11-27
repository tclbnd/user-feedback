import unittest
import zlib

from dxakit.dxa import DxaError, parse


def seq(w, h, frames):
    return b"DXASEQ\0\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + len(frames).to_bytes(4, "little") \
        + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_key_frame(self):
        pixels = bytes(range(16))
        frame = b"FRAM" + bytes([2]) + (16).to_bytes(4, "big") + zlib.compress(pixels)
        out = parse(seq(4, 4, [frame]))
        self.assertTrue(out["frames"][0]["ok"])
        self.assertEqual(out["colours"], 16)

    def test_odd_size(self):
        with self.assertRaises(DxaError):
            parse(seq(6, 4, []))


if __name__ == "__main__":
    unittest.main()
