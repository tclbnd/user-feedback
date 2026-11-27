import unittest

from rl2kit.rl2 import Rl2Error, parse


def pak(w, h, frames):
    return b"RL2PAK\0\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes(2) + bytes(768) \
        + bytes(4) + len(frames).to_bytes(2, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_single_pixels(self):
        out = parse(pak(2, 2, [bytes([1, 2, 3, 4])]))
        self.assertEqual(out["background"], False)
        self.assertEqual(len(out["frames"]), 1)

    def test_bad_magic(self):
        with self.assertRaises(Rl2Error):
            parse(b"RL2XXX\0\0" + bytes(800))


if __name__ == "__main__":
    unittest.main()
