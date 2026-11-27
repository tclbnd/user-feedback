import unittest

from hnmkit.hnm4 import HnmError, parse


def seq(version, w, h, chunks):
    return b"HNM4SEQ\0" + bytes([version, 0]) + w.to_bytes(2, "little") + h.to_bytes(2, "little") \
        + len(chunks).to_bytes(2, "little") + b"".join(len(c).to_bytes(4, "little") + c for c in chunks)


class BasicTests(unittest.TestCase):
    def test_sound_chunk_skipped(self):
        out = parse(seq(0x48, 8, 4, [bytes([12, 0, 0, 0]) + b"SD" + bytes(6)]))
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(out["frames"], [])

    def test_odd_height(self):
        with self.assertRaises(HnmError):
            parse(seq(0x48, 8, 5, []))


if __name__ == "__main__":
    unittest.main()
