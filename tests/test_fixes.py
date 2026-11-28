import struct
import unittest

from pafkit.paf import Paf, parse

W, H = 256, 32
VSIZE = W * H


def pack(frames):
    out = bytearray(b"PAFPACK1")
    out += struct.pack("<HHH", W, H, len(frames))
    for pkt in frames:
        out += struct.pack("<I", len(pkt)) + pkt
    return bytes(out)


def pal_frame(index, rgb6):
    # kind 2 with 0x40 palette flag: copy page 0, then update the palette.
    r, g, b = rgb6
    return bytes([0x42, index, 0, r, g, b])


def rle_frame(runs):
    pkt = bytearray([0x04, 0, 0])
    for c, payload in runs:
        pkt.append(c & 0xFF)
        pkt += payload
    return bytes(pkt)


class PaletteTests(unittest.TestCase):
    def test_six_bit_expansion_replicates_top_bits(self):
        v = Paf(W, H)
        v.frame(pal_frame(0, (0x3F, 0x3F, 0x3F)))
        self.assertEqual(v.pal[0], (255, 255, 255))

    def test_partial_values(self):
        v = Paf(W, H)
        v.frame(pal_frame(10, (0x10, 0x20, 0x01)))
        self.assertEqual(v.pal[10], ((0x10 << 2) | 1, (0x20 << 2) | 2, (0x01 << 2) | 0))


class RleTests(unittest.TestCase):
    def test_run_count_is_plus_one(self):
        frame = rle_frame([(0x81, b"\x2A")] * (VSIZE // 2))
        v = Paf(W, H)
        v.frame(frame)
        self.assertEqual(bytes(v.pages[0][:VSIZE]), b"\x2A" * VSIZE)

    def test_literal_run_plus_one(self):
        # Pure literal runs: count byte 1 means two raw pixels each.
        pixels = bytes([0x33, 0x44] + [0x00] * (VSIZE - 2))
        frame = rle_frame([(0x01, pixels[i:i + 2]) for i in range(0, VSIZE, 2)])
        v = Paf(W, H)
        v.frame(frame)
        self.assertEqual(v.pages[0][0], 0x33)
        self.assertEqual(v.pages[0][1], 0x44)


class PageRotationTests(unittest.TestCase):
    def test_pages_advance_and_page_copy_references_old_page(self):
        raw = bytes([0x01]) + bytes(range(256)) * (VSIZE // 256)
        copy_p2 = bytes([0x02, 0x02])  # type 2, reference page 2
        out = parse(pack([raw, raw, copy_p2]))
        self.assertEqual([f["state"] for f in out["frames"]], ["decoded"] * 3)
        # frame 2 copies page 2, which holds frame 0
        self.assertEqual(out["frames"][2]["crc32"], out["frames"][0]["crc32"])

    def test_four_pages_wrap(self):
        v = Paf(W, H)
        raw = bytes([0x01]) + b"\x55" * VSIZE
        for _ in range(5):
            v.frame(raw)
        # five non-key frames: 0 -> 1 -> 2 -> 3 -> 0 -> 1
        self.assertEqual(v.cur, 1)


class Decode0Tests(unittest.TestCase):
    def _type0(self, opcode_byte):
        pkt = bytearray([0x00, 0x00])  # no preset blocks
        pkt += b"\x00\x00" * (VSIZE // 16)  # all block-copy srcs: page 0 (0,0)
        ops = bytearray(VSIZE // 32)
        ops[0] = opcode_byte
        pkt += struct.pack("<H", len(ops)) + b"\x00\x00" + bytes(ops)
        return pkt

    def test_opcode_nibbles_are_big_endian(self):
        color = 0x7C
        pkt = self._type0(0x10)
        pkt += bytes([color, 0xFF, color, 0xFF])  # opc 1: two color+mask rows
        v = Paf(W, H)
        v.frame(pkt)
        cur = v.pages[0]
        self.assertEqual(list(cur[0:4]), [color] * 4)
        self.assertEqual(list(cur[W:W + 4]), [color] * 4)
        # the adjacent second block stays as the page copy, not recolored
        self.assertEqual(list(cur[4:8]), [0] * 4)

    def test_source_position_is_in_block_units(self):
        v = Paf(W, H)
        v.cur = 1  # decode into page 1 so the source page 0 stays untouched
        # marker at pixel (5, 5): inside source block origin (4, 4), top half
        v.pages[0][5 + 5 * W] = 0x99
        pkt = self._type0(0x50)  # opc 5: one mask copy of the top half
        pkt += b"\x01\x02" + bytes([0xFF])  # src page 0, x block 2, y block 2
        v.frame(pkt)
        cur = v.pages[1]
        self.assertEqual(cur[1 + W], 0x99)  # lands in the first block


if __name__ == "__main__":
    unittest.main()
