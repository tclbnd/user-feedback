import unittest

from bfikit.bfi import BfiError, parse


def movie(w, h, frames):
    return b"BFIMOVIE" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes(2) + len(frames).to_bytes(2, "little") \
        + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_plain_bytes(self):
        out = parse(movie(2, 2, [bytes(4) + bytes([4, 1, 2, 3, 4, 0x80, 0, 0])]))
        self.assertEqual(out["frames"][0]["state"], "decoded")

    def test_bad_magic(self):
        with self.assertRaises(BfiError):
            parse(b"NOTABFI!" + bytes(16))


if __name__ == "__main__":
    unittest.main()
