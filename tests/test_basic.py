import unittest

from seqkit.tiertex import SeqError, parse


def tsq(frames):
    return b"TSEQVID\0" + len(frames).to_bytes(4, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_single_pixel(self):
        ops = bytes([0xC0]) + bytes(127)
        out = parse(tsq([bytes([2]) + ops + bytes([0x80, 7])]))
        self.assertTrue(out["frames"][0]["ok"])
        self.assertEqual(out["colours"], 2)

    def test_not_seq(self):
        with self.assertRaises(SeqError):
            parse(b"TSEQVIX\0" + bytes(4))


if __name__ == "__main__":
    unittest.main()
