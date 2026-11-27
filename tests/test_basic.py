import unittest

from yopkit.yop import YopError, parse


def ysq(w, h, frames, ncol=0):
    return b"YOPSEQ\0\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([ncol, 0, 0]) \
        + len(frames).to_bytes(4, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_flat_block(self):
        frame = bytes([0, 0, 0, 0, 0xDD, 7])
        out = parse(ysq(2, 2, [frame]))
        self.assertTrue(out["frames"][0]["ok"])
        self.assertEqual(out["colours"], 1)

    def test_odd_width(self):
        with self.assertRaises(YopError):
            parse(ysq(3, 2, []))


if __name__ == "__main__":
    unittest.main()
