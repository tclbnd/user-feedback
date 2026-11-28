import struct
import unittest

from argokit.argo import Argo, ArgoError, In, parse


def pack(w, h, chunks):
    out = b"ARGOPACK" + struct.pack("<HHH", w, h, len(chunks))
    for c in chunks:
        out += struct.pack("<I", len(c)) + c
    return out


def mad1(body):
    return b"MAD1" + body + b"\xff"


class BasicTests(unittest.TestCase):
    def test_example_chunks(self):
        with open("examples/movie.brp", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], len(out["frames"])), (64, 32, 5))
        self.assertEqual([f["state"] for f in out["frames"]],
                         ["palette", "decoded", "decoded", "decoded", "decoded"])

    def test_bad_magic(self):
        with self.assertRaises(ArgoError):
            parse(bytes(16))

    def test_fill2x2_covers_both_rows(self):
        out = parse(pack(4, 4, [mad1(bytes([5, 1, 2, 3, 4]))]))
        self.assertEqual(out["frames"][0]["state"], "decoded")
        v = Argo(4, 4)
        v.mad1(In(bytes([5, 1, 2, 3, 4, 0xFF])))
        self.assertEqual(list(v.pix), [1, 1, 2, 2] * 2 + [3, 3, 4, 4] * 2)

    def test_motion_offsets_are_signed(self):
        v = Argo(16, 16)
        v.pix[:] = bytes(range(256))
        # move the 8x8 block at (0, 0) to (8, 8): offsets 64-8 = 56 each way
        rec = bytes([8, 0, 1, 1, 1, 0x1C, 0x38, 0])
        v.mad1(In(bytes([7]) + rec + b"\xff"))
        for r in range(8):
            src = list(v.pix[r * 16:r * 16 + 8])
            self.assertEqual(list(v.pix[(8 + r) * 16 + 8:(8 + r) * 16 + 16]), src)

    def test_coded_delta_lsb_first(self):
        v = Argo(4, 4)
        # control 0x00: no skip, one group of 4 codes; codes 3,1,1,0 (LSB first)
        v.mad1(In(bytes([2, 0x00, 0b00010111, 0x2A, 0xFF])))
        self.assertEqual(list(v.pix[:4]), [0x2A, 0x2A, 0x2A, 0])

    def test_rle_skip_pairs(self):
        v = Argo(4, 4)
        v.rle(In(bytes([0, 3, 2, 9, 0, 4, 1, 7])))
        self.assertEqual(list(v.pix[:8]), [0, 0, 0, 9, 9, 0, 0, 0])
        self.assertEqual(v.pix[9], 7)

    def test_avcf_draws_every_block(self):
        table = bytearray(1024)
        table[4:8] = bytes([1, 2, 3, 4])
        chunk = b"AVCF" + bytes(table) + bytes([1, 0, 0, 1])
        out = parse(pack(4, 4, [chunk]))
        self.assertEqual(out["frames"][0]["state"], "decoded")
        v = Argo(4, 4)
        v.blocks(In(bytes(table) + bytes([1, 0, 0, 1])), masked=False)
        self.assertEqual(list(v.pix), [1, 2, 0, 0, 3, 4, 0, 0,
                                       0, 0, 1, 2, 0, 0, 3, 4])

    def test_alcd_draws_only_masked_blocks(self):
        table = bytearray(1024)
        table[4:8] = bytes([1, 2, 3, 4])
        # mask 0b0101: blocks 0 and 2 of the 2x2 block grid
        v = Argo(4, 4)
        v.blocks(In(bytes(table) + bytes([0b0101, 1, 1])), masked=True)
        self.assertEqual(list(v.pix), [1, 2, 0, 0, 3, 4, 0, 0,
                                       1, 2, 0, 0, 3, 4, 0, 0])

    def test_truncated_block_map_is_an_error_frame(self):
        out = parse(pack(4, 4, [b"AVCF" + bytes(100)]))
        self.assertEqual(out["frames"][0],
                         {"state": "error", "crc32": None})
        out = parse(pack(4, 4, [b"ALCD" + bytes(1024)]))
        self.assertEqual(out["frames"][0],
                         {"state": "error", "crc32": None})


if __name__ == "__main__":
    unittest.main()
