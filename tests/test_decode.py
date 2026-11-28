import struct
import unittest
import zlib

from arbckit.arbc import SIZES, Arbc, ArbcError, parse

W, H = 256, 256


def le16(v):
    return struct.pack("<H", v)


def tile(row, col, mask):
    return bytes((row, col)) + le16(mask)


def decode_pack(packets):
    n = 14 + sum(4 + len(p) for p in packets)
    out = b"ARBCPACK" + struct.pack("<HHH", W, H, len(packets))
    for p in packets:
        out += struct.pack("<I", len(p)) + p
    return out


def run(packets):
    v = Arbc(W, H)
    return [v.packet(p) for p in packets]


def rgb_crc(pix):
    return "%08x" % zlib.crc32(b"".join(bytes(p) for p in pix))


class ColorTests(unittest.TestCase):
    def test_channels_are_u16_with_padding(self):
        # R=7, G=11, B=13, one size-4 tile fully masked at top-left.
        seg = le16(7) + le16(11) + le16(13) + bytes((0x01,)) + le16(1) + tile(63, 0, 0xFFFF)
        pix = run([b"\x00" * 8 + le16(1) + seg])[0]
        # Bottom tile row 31 maps to the top of the picture.
        for y in range(4):
            for x in range(4):
                self.assertEqual(pix[y * W + x], (7, 11, 13))
        self.assertEqual(pix[4 * W], (0, 0, 0))

    def test_high_bytes_are_padding_not_channels(self):
        # High byte of each little-endian u16 is padding and must be ignored.
        # R bytes 0F 0A -> 15; G bytes FF 01 -> 255; B bytes 01 00 -> 1.
        color = bytes((0x0F, 0x0A, 0xFF, 0x01, 0x01, 0x00))
        seg = color + bytes((0x01,)) + le16(1) + tile(0, 63, 0x0001)
        pix = run([b"\x00" * 8 + le16(1) + seg])[0]
        self.assertEqual(pix[(H - 1) * W + (W - 1)], (15, 255, 1))


class OrientationTests(unittest.TestCase):
    def test_tile_rows_bottom_first(self):
        # A full tile in stored row 0 must land at the BOTTOM of the picture.
        seg = le16(9) * 3 + bytes((0x01,)) + le16(1) + tile(0, 0, 0xFFFF)
        pix = run([b"\x00" * 8 + le16(1) + seg])[0]
        self.assertEqual(pix[(H - 1) * W], (9, 9, 9))
        self.assertEqual(pix[0], (0, 0, 0))

    def test_mask_bit0_is_bottom_right_subtile(self):
        # Size-4 tile at stored row 0/col 31: only bit 0 set paints exactly the
        # single bottom-right pixel of the tile (bottom-right of the picture).
        seg = le16(5) * 3 + bytes((0x01,)) + le16(1) + tile(0, 63, 0x0001)
        pix = run([b"\x00" * 8 + le16(1) + seg])[0]
        self.assertEqual(pix[(H - 1) * W + (W - 1)], (5, 5, 5))
        painted = [i for i, p in enumerate(pix) if p == (5, 5, 5)]
        self.assertEqual(painted, [(H - 1) * W + W - 1])

    def test_asymmetric_mask_orientation(self):
        # mask 0x8cef on a size-4 tile gives a staircase anchored top-left.
        seg = le16(2) * 3 + bytes((0x01,)) + le16(1) + tile(0, 63, 0x8CEF)
        pix = run([b"\x00" * 8 + le16(1) + seg])[0]
        expected = [
            "#...",
            "##..",
            "###.",
            "####",
        ]
        for ry, line in enumerate(expected):
            y = H - 4 + ry
            for cx, ch in enumerate(line):
                x = W - 4 + cx
                want = (2, 2, 2) if ch == "#" else (0, 0, 0)
                self.assertEqual(pix[y * W + x], want, (ry, cx))


class BlockSizeTests(unittest.TestCase):
    def test_all_five_sizes_paint_full_area(self):
        for bit, edge in SIZES:
            # Fully masked tile at stored row/col 0 occupies the bottom-left
            # edge x edge corner of the picture.
            seg = le16(20) * 3 + bytes((bit,)) + le16(1) + tile(0, 0, 0xFFFF)
            pix = run([b"\x00" * 8 + le16(1) + seg])[0]
            count = sum(1 for p in pix if p == (20, 20, 20))
            self.assertEqual(count, edge * edge, edge)
            self.assertEqual(pix[(H - 1) * W], (20, 20, 20))
            self.assertEqual(pix[(H - edge) * W], (20, 20, 20))
            self.assertEqual(pix[(H - edge) * W + edge - 1], (20, 20, 20))
            self.assertEqual(pix[(H - edge - 1) * W], (0, 0, 0))

    def test_each_mask_bit_scales_with_tile_edge(self):
        # For every tile edge, one mask bit paints an (edge/4)^2 sub-tile;
        # bit 0 is the bottom-right sub-tile.
        for bit, edge in SIZES:
            seg = le16(30) * 3 + bytes((bit,)) + le16(1) + tile(0, 0, 0x0001)
            pix = run([b"\x00" * 8 + le16(1) + seg])[0]
            scale = edge // 4
            count = sum(1 for p in pix if p == (30, 30, 30))
            self.assertEqual(count, scale * scale, edge)
            self.assertEqual(pix[(H - 1) * W + edge - 1], (30, 30, 30))
            self.assertEqual(pix[(H - scale) * W + edge - scale], (30, 30, 30))
            self.assertEqual(pix[(H - scale - 1) * W + edge - scale], (0, 0, 0))

    def test_largest_tile_paints_more_than_top_left(self):
        # Flag 0x10 selects the 64x64 tile: a full mask paints 4096 pixels,
        # reaching well beyond the top-left sub-tile.
        seg = le16(40) * 3 + bytes((0x10,)) + le16(1) + tile(0, 0, 0xFFFF)
        pix = run([b"\x00" * 8 + le16(1) + seg])[0]
        self.assertEqual(sum(1 for p in pix if p == (40, 40, 40)), 64 * 64)
        self.assertEqual(pix[(H - 1) * W + 63], (40, 40, 40))
        self.assertEqual(pix[(H - 64) * W + 63], (40, 40, 40))
        self.assertEqual(pix[(H - 64) * W + 64], (0, 0, 0))


class PersistenceTests(unittest.TestCase):
    def test_second_packet_paints_over_previous_frame(self):
        # Full size-4 tile at the top-left of the picture (stored row 31).
        first = le16(60) * 3 + bytes((0x01,)) + le16(1) + tile(63, 0, 0xFFFF)
        # Next packet changes only that tile's bottom-right sub-pixel.
        second = le16(70) * 3 + bytes((0x01,)) + le16(1) + tile(63, 0, 0x0001)
        p0, p1 = run([
            b"\x00" * 8 + le16(1) + first,
            b"\x00" * 8 + le16(1) + second,
        ])
        # The rest of the tile keeps the previous frame's colour.
        self.assertEqual(p1[0], (60, 60, 60))
        self.assertEqual(p1[3 * W + 3], (70, 70, 70))
        self.assertEqual(p1[3 * W + 2], (60, 60, 60))
        # Nothing outside the tile ever gets painted.
        self.assertEqual(p1[4 * W], (0, 0, 0))

    def test_zero_segment_packet_is_skipped_but_keeps_frame(self):
        first = le16(60) * 3 + bytes((0x01,)) + le16(1) + tile(63, 0, 0xFFFF)
        p0, p1, p2 = run([
            b"\x00" * 8 + le16(1) + first,
            b"\x00" * 8 + le16(0),
            b"\x00" * 8 + le16(0),
        ])
        self.assertIsNone(p1)
        self.assertIsNone(p2)
        # Frame content stays available for later packets.
        third = le16(80) * 3 + bytes((0x01,)) + le16(1) + tile(63, 0, 0x0001)
        v = Arbc(W, H)
        frames = [v.packet(p) for p in (
            b"\x00" * 8 + le16(1) + first,
            b"\x00" * 8 + le16(0),
            b"\x00" * 8 + le16(1) + third,
        )]
        self.assertEqual(frames[2][0], (60, 60, 60))
        self.assertEqual(frames[2][3 * W + 3], (80, 80, 80))

    def test_parse_reports_skipped_and_matches_crc(self):
        first = b"\x00" * 8 + le16(1) + le16(60) * 3 + bytes((0x01,)) + le16(1) + tile(63, 0, 0xFFFF)
        pack = decode_pack([first, b"\x00" * 8 + le16(0)])
        out = parse(pack)
        self.assertEqual([f["state"] for f in out["frames"]], ["decoded", "skipped"])
        v = Arbc(W, H)
        pix = v.packet(first)
        self.assertEqual(out["frames"][0]["crc32"], rgb_crc(pix))


class ErrorTests(unittest.TestCase):
    def test_short_packet(self):
        with self.assertRaises(ArbcError):
            Arbc(W, H).packet(b"\x00" * 4)

    def test_parse_bad_magic(self):
        with self.assertRaises(ArbcError):
            parse(bytes(16))

    def test_fully_out_of_range_tile_is_ignored(self):
        seg = le16(1) * 3 + bytes((0x01,)) + le16(1) + tile(200, 200, 0xFFFF)
        pix = run([b"\x00" * 8 + le16(1) + seg])[0]
        self.assertTrue(all(p == (0, 0, 0) for p in pix))

    def test_tile_crossing_top_edge_is_ignored(self):
        # A 64x64 tile at stored row 4 starts 64 px above the top edge: the
        # codec drops the whole tile rather than clipping it.
        seg = le16(50) * 3 + bytes((0x10,)) + le16(1) + tile(4, 0, 0xFFFF)
        pix = run([b"\x00" * 8 + le16(1) + seg])[0]
        self.assertTrue(all(p == (0, 0, 0) for p in pix))
        # The last row that still fits (row 3, origin at y=0) is painted.
        seg_ok = le16(50) * 3 + bytes((0x10,)) + le16(1) + tile(3, 0, 0xFFFF)
        pix_ok = run([b"\x00" * 8 + le16(1) + seg_ok])[0]
        self.assertEqual(sum(1 for p in pix_ok if p == (50, 50, 50)), 64 * 64)

    def test_tile_crossing_right_edge_is_ignored(self):
        # A size-4 tile at col 64 crosses the right edge (x=256); dropped.
        over = le16(60) * 3 + bytes((0x01,)) + le16(1) + tile(0, 64, 0xFFFF)
        pix = run([b"\x00" * 8 + le16(1) + over])[0]
        self.assertTrue(all(p == (0, 0, 0) for p in pix))
        # The last fitting column (63) paints the full 4x4 tile.
        fit = le16(60) * 3 + bytes((0x01,)) + le16(1) + tile(0, 63, 0xFFFF)
        pix_fit = run([b"\x00" * 8 + le16(1) + fit])[0]
        self.assertEqual(sum(1 for p in pix_fit if p == (60, 60, 60)), 16)


if __name__ == "__main__":
    unittest.main()
