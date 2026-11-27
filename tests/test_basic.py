import unittest

from avskit.avs import AvsError, parse


def movie(frames):
    return b"AVSMOVIE" + len(frames).to_bytes(2, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_key_frame(self):
        frame = bytes([0, 1, 0, 0]) + bytes(256 * 9) + bytes(106 * 66)
        out = parse(movie([frame]))
        self.assertEqual(out["frames"][0]["ok"], True)
        self.assertEqual(out["frames"][0]["key"], True)

    def test_bad_magic(self):
        with self.assertRaises(AvsError):
            parse(b"AVIMOVIE" + bytes(10))


if __name__ == "__main__":
    unittest.main()
