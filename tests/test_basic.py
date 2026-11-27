import unittest

from dk3kit.dk3 import Dk3Error, parse


def stream(blocks, align):
    return b"DK3SOUND" + (22050).to_bytes(4, "little") + (2).to_bytes(2, "little") + align.to_bytes(2, "little") + b"".join(blocks)


class BasicTests(unittest.TestCase):
    def test_silent_block(self):
        out = parse(stream([bytes(16) + bytes(12)], 28))
        self.assertEqual(out["blocks"], 1)
        self.assertEqual(out["left"]["peak"], out["right"]["peak"])

    def test_bad_magic(self):
        with self.assertRaises(Dk3Error):
            parse(b"WAVEFORM" + bytes(40))


if __name__ == "__main__":
    unittest.main()
