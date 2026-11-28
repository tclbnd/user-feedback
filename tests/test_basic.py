import unittest

from hcomkit.hcom import HcomError, parse


def pack(extra, chunks):
    return b"HCOMPACK" + (11025).to_bytes(2, "little") + len(extra).to_bytes(2, "little") + extra \
        + len(chunks).to_bytes(2, "little") + b"".join(len(c).to_bytes(4, "little") + c for c in chunks)


def s16(v):
    return (v & 0xFFFF).to_bytes(2, "big")


class BasicTests(unittest.TestCase):
    def test_two_leaf_tree(self):
        nodes = [(1, 2), (-1, 0), (-1, 0)]
        extra = len(nodes).to_bytes(2, "big") + (1).to_bytes(4, "big") + b"".join(s16(l) + s16(r) for l, r in nodes) + b"\x80"
        out = parse(pack(extra, [b"\x00"]))
        self.assertEqual(out["entries"], 3)
        self.assertEqual(out["samples"], 8)

    def test_bad_magic(self):
        with self.assertRaises(HcomError):
            parse(b"HCOMFILE" + bytes(8))


if __name__ == "__main__":
    unittest.main()
