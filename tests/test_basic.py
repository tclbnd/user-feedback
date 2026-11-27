import unittest

from kmvckit.kmvc import KmvcError, parse


def seq(w, h, frames, palsize=127):
    return b"KMVCSEQ\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + palsize.to_bytes(2, "little") \
        + len(frames).to_bytes(2, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_bad_blocksize(self):
        out = parse(seq(8, 8, [bytes([0x83, 4])]))
        self.assertFalse(out["frames"][0]["ok"])

    def test_too_wide(self):
        with self.assertRaises(KmvcError):
            parse(seq(640, 8, []))


if __name__ == "__main__":
    unittest.main()
