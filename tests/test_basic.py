import unittest

from smckit.smc import SmcError, parse


def seq(w, h, frames):
    return b"SMCSEQ\0\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + len(frames).to_bytes(4, "little") \
        + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_one_colour(self):
        frame = bytes([0, 0, 0, 6, 0x60, 0x2A])
        out = parse(seq(4, 4, [frame]))
        self.assertTrue(out["frames"][0]["ok"])
        self.assertEqual(out["top"], [[42, 16]])

    def test_bad_size(self):
        with self.assertRaises(SmcError):
            parse(seq(6, 4, []))


if __name__ == "__main__":
    unittest.main()
