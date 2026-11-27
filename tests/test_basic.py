import unittest

from qtrlekit.qtrle import QtrleError, parse


def seq(w, h, depth, frames):
    return b"QTRLESEQ" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([depth]) + len(frames).to_bytes(3, "little") \
        + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_no_frames(self):
        out = parse(seq(4, 2, 8, []))
        self.assertEqual(out["frames"], [])
        self.assertEqual(out["colours"], 1)

    def test_bad_depth(self):
        with self.assertRaises(QtrleError):
            parse(seq(4, 2, 3, []))


if __name__ == "__main__":
    unittest.main()
