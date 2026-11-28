import unittest
import zlib

from hcomkit.hcom import HcomError, parse


def pack(extra, chunks):
    return b"HCOMPACK" + (11025).to_bytes(2, "little") + len(extra).to_bytes(2, "little") + extra \
        + len(chunks).to_bytes(2, "little") + b"".join(len(c).to_bytes(4, "little") + c for c in chunks)


def s16(v):
    return (v & 0xFFFF).to_bytes(2, "big")


def header(nodes, delta=0, first=128):
    return len(nodes).to_bytes(2, "big") + delta.to_bytes(4, "big") \
        + b"".join(s16(l) + s16(r) for l, r in nodes) + bytes([first])


def decode(nodes, chunks, delta=0, first=128):
    return parse(pack(header(nodes, delta, first), chunks))


def crc(samples):
    return "%08x" % zlib.crc32(bytes(samples))


class BasicTests(unittest.TestCase):
    def test_two_leaf_tree(self):
        nodes = [(1, 2), (-1, 0), (-1, 0)]
        out = decode(nodes, [b"\x00"])
        self.assertEqual(out["entries"], 3)
        self.assertEqual(out["chunks"], [8])
        self.assertEqual(out["samples"], 9)

    def test_bad_magic(self):
        with self.assertRaises(HcomError):
            parse(b"HCOMFILE" + bytes(8))

    def test_bits_are_read_most_significant_first(self):
        nodes = [(1, 2), (-1, 0), (-1, 5)]
        out = decode(nodes, [b"\x80"])
        self.assertEqual(out["chunks"], [8])
        self.assertEqual(out["samples"], 9)
        self.assertEqual(out["crc32"], crc([128, 133] + [128] * 7))

    def test_first_sample_comes_from_the_header(self):
        nodes = [(1, 2), (-1, 0), (-1, 0)]
        out = decode(nodes, [b"\xff"], first=90)
        self.assertEqual(out["crc32"], crc([90] + [128] * 8))

    def test_without_delta_values_are_offsets_from_silence(self):
        nodes = [(1, 2), (-1, 0), (-1, -9)]
        out = decode(nodes, [b"\xff"], delta=0, first=128)
        self.assertFalse(out["delta"])
        self.assertEqual(out["crc32"], crc([128] + [119] * 8))

    def test_delta_accumulates_and_carries_between_chunks(self):
        nodes = [(1, 2), (-1, 1), (-1, -1)]
        out = decode(nodes, [b"\x00", b"\xff"], delta=1, first=200)
        self.assertEqual(out["chunks"], [8, 8])
        self.assertEqual(out["crc32"], crc([200] + list(range(201, 209)) + list(range(207, 199, -1))))


if __name__ == "__main__":
    unittest.main()
