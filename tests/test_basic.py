import struct
import unittest

from pkmtex.etc import PkmError, parse


class BasicTests(unittest.TestCase):
    def test_flat_individual_block(self):
        # one individual-mode block, both halves colour 8/8/8, table 0, every index 0
        q = struct.pack(">Q", (0x88 << 24 | 0x88 << 16 | 0x88 << 8) << 32)
        out = parse(b"PKM 10" + struct.pack(">HHHHH", 0, 4, 4, 4, 4) + q)
        self.assertEqual(out["format"], "ETC1")
        self.assertEqual(out["modes"]["individual"], 1)
        self.assertEqual(out["first_row"][0][1], 4)

    def test_not_a_texture(self):
        with self.assertRaises(PkmError):
            parse(b"KTX 11" + bytes(40))


if __name__ == "__main__":
    unittest.main()
