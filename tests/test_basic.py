import unittest

from rlekit.msrle import RleError, parse


def pack(w, h, depth, frames):
    return b"MSRLEPAK" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([depth, 0]) + len(frames).to_bytes(2, "little") \
        + bytes(1024) + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_single_run(self):
        out = parse(pack(4, 1, 8, [bytes([4, 7, 0, 1])]))
        self.assertEqual(out["frames"][0]["state"], "decoded")

    def test_bad_depth(self):
        with self.assertRaises(RleError):
            parse(pack(4, 1, 16, []))


if __name__ == "__main__":
    unittest.main()
