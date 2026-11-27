import unittest

from anmkit.anm import AnmError, parse


def pack(w, h, records):
    return b"DPANMSEQ" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + len(records).to_bytes(2, "little") + bytes(2) \
        + bytes(1024) + b"".join(len(r).to_bytes(4, "little") + r for r in records)


class BasicTests(unittest.TestCase):
    def test_stop_only(self):
        out = parse(pack(4, 4, [bytes([0x42, 0, 0, 0, 0x80, 0, 0])]))
        self.assertEqual(out["frames"][0]["ok"], True)

    def test_bad_magic(self):
        with self.assertRaises(AnmError):
            parse(b"LPF ANIM" + bytes(1100))


if __name__ == "__main__":
    unittest.main()
