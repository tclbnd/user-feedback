import unittest

from sndkit.snd1 import SndError, parse


def pack(chunks):
    return b"SND1PACK" + (22050).to_bytes(2, "little") + len(chunks).to_bytes(2, "little") + b"".join(len(c).to_bytes(4, "little") + c for c in chunks)


class BasicTests(unittest.TestCase):
    def test_run_chunk(self):
        out = parse(pack([(4).to_bytes(2, "little") + (5).to_bytes(2, "little") + bytes([0xC3])]))
        self.assertEqual(out["chunks"], [4])
        self.assertEqual(out["sum"], 0)

    def test_bad_magic(self):
        with self.assertRaises(SndError):
            parse(b"VOCPACK!" + bytes(8))


if __name__ == "__main__":
    unittest.main()
