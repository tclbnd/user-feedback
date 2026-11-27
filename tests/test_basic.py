import unittest

from rpzakit.rpza import RpzaError, parse


def seq(w, h, frames):
    return b"RPZASEQ\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + len(frames).to_bytes(4, "little") \
        + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


class BasicTests(unittest.TestCase):
    def test_single_fill(self):
        frame = bytes([0xE1, 0, 0, 7, 0xA0, 0x7C, 0x00])
        out = parse(seq(4, 4, [frame]))
        self.assertTrue(out["frames"][0]["ok"])
        self.assertEqual(out["colours"], 1)

    def test_bad_height(self):
        with self.assertRaises(RpzaError):
            parse(seq(4, 6, []))


if __name__ == "__main__":
    unittest.main()
