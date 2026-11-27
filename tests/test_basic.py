import unittest

from vmdkit.vmd import VmdError, parse, lz_unpack, Decoder


def frames_file(w, h, recs, lz=0):
    return b"VMDFRAME" + w.to_bytes(2, "little") + h.to_bytes(2, "little") \
        + lz.to_bytes(4, "little") + len(recs).to_bytes(2, "little") \
        + b"".join(len(r).to_bytes(4, "little") + r for r in recs)


def rect(x, y, right, bottom, flags=0):
    return bytes(6) + x.to_bytes(2, "little") + y.to_bytes(2, "little") \
        + right.to_bytes(2, "little") + bottom.to_bytes(2, "little") \
        + b"\x00" + bytes([flags])


def raw_frame(x, y, right, bottom, pixels):
    return rect(x, y, right, bottom) + b"\x02" + bytes(pixels)


def rle_frame(x, y, right, bottom, stream, method=1):
    return rect(x, y, right, bottom) + bytes([method]) + bytes(stream)


def lz_frame(x, y, right, bottom, unpacked, method):
    payload = lz_pack(unpacked)
    return rect(x, y, right, bottom) + bytes([method | 0x80]) + payload


def lz_pack(unpacked):
    # Encode every byte as a literal; one flag byte per eight payload bytes.
    out = len(unpacked).to_bytes(4, "little") + (0x56781234).to_bytes(4, "little")
    for i in range(0, len(unpacked), 8):
        chunk = unpacked[i:i + 8]
        out += bytes([0xFF >> (8 - len(chunk))])
        out += bytes(chunk)
    return out


def picture(recs, w=8, h=4, lz=0):
    dec = Decoder(w, h, lz)
    for rec in recs:
        dec.frame(rec)
    return dec


class BasicTests(unittest.TestCase):
    def test_no_frames(self):
        out = parse(frames_file(8, 4, []))
        self.assertEqual(out["frames"], [])

    def test_empty_picture(self):
        with self.assertRaises(VmdError):
            parse(frames_file(0, 4, []))

    # The right and bottom coordinates are inclusive.
    def test_inclusive_rectangle_is_one_pixel(self):
        # A 4x2 picture whose anchor frame paints only its local origin.
        rec = raw_frame(0, 0, 0, 0, [7])
        dec = picture([rec], w=4, h=2)
        self.assertEqual(dec.prev[0], 7)
        self.assertEqual(sum(dec.prev), 7)  # only one pixel changed

    def test_inclusive_rectangle_offset_pixel(self):
        # Anchor at screen (2,1); a later frame paints one cell at local (1,0).
        first = raw_frame(2, 1, 5, 2, list(range(8)))
        patch = raw_frame(3, 1, 3, 1, [9])
        dec = picture([first, patch], w=4, h=2)
        self.assertEqual(dec.prev[1], 9)
        self.assertEqual(dec.prev[0], 0)  # neighbouring cell untouched

    def test_inclusive_full_frame(self):
        pixels = list(range(32))
        dec = picture([raw_frame(0, 0, 7, 3, pixels)], w=8, h=4)
        self.assertEqual(bytes(dec.prev), bytes(pixels))

    # A frame only paints its rectangle; everything else is retained.
    def test_partial_region_keeps_previous_picture(self):
        full = raw_frame(0, 0, 7, 3, list(range(32)))
        patch = raw_frame(1, 1, 2, 2, [9, 9, 9, 9])
        dec = picture([full, patch])
        expected = bytearray(range(32))
        expected[1 * 8 + 1:1 * 8 + 3] = b"\x09\x09"
        expected[2 * 8 + 1:2 * 8 + 3] = b"\x09\x09"
        self.assertEqual(bytes(dec.prev), bytes(expected))

    # A failed frame must not alter the current picture.
    def test_bad_frame_is_reported_and_keeps_picture(self):
        good = raw_frame(0, 0, 7, 3, list(range(32)))
        # A single byte cannot serve four rows, so the frame fails.
        bad = rle_frame(0, 0, 7, 3, [0x80, 1], method=1)
        out = parse(frames_file(8, 4, [good, bad]))
        self.assertTrue(out["frames"][0]["ok"])
        self.assertFalse(out["frames"][1]["ok"])
        self.assertIsNone(out["frames"][1]["crc32"])

    # Absolute screen coordinates: the first frame anchors the movie offset.
    def test_fixed_offset_second_frame_lands_locally(self):
        first = raw_frame(10, 20, 17, 23, list(range(8 * 4)))
        # Same absolute origin, a 2x2 patch in local coordinates (2, 2).
        second = rle_frame(12, 22, 13, 23, [0x81, 5, 6, 0x81, 7, 8])
        dec = picture([first, second])
        base = 2 * 8 + 2
        self.assertEqual(dec.prev[base:base + 2], bytes([5, 6]))
        self.assertEqual(dec.prev[base + 8:base + 10], bytes([7, 8]))

    # Method 1: high bit literal run, low bit copy-from-previous run.
    def test_method1_literal_and_copy(self):
        full = raw_frame(0, 0, 7, 3, list(range(32)))
        # literal 3 pixels, then copy 5 from the previous frame, per row.
        stream = [0x82, 1, 2, 3, 0x04] * 4
        dec = picture([full, rle_frame(0, 0, 7, 3, stream, method=1)])
        for row in range(4):
            base = row * 8
            self.assertEqual(dec.prev[base:base + 3], bytes([1, 2, 3]))
            self.assertEqual(dec.prev[base + 3:base + 8],
                             bytes(range(32))[base + 3:base + 8])

    # Method 3 adds a 0xFF solid run: count (+1) and one colour byte.
    def test_method3_solid_run(self):
        full = raw_frame(0, 0, 7, 0, list(range(8)))
        # solid 7 nines: 0xFF, count 6, colour 9
        stream = [0xFF, 0x06, 9]
        dec = picture([full, rle_frame(0, 0, 7, 0, stream, method=3)], w=8, h=1)
        self.assertEqual(dec.prev[0:7], bytes([9] * 7))

    def test_method3_solid_count_is_plus_one(self):
        full = raw_frame(0, 0, 7, 0, list(range(8)))
        # count byte 0 means one pixel, proving the +1 adjustment.
        stream = [0xFF, 0x00, 5]
        dec = picture([full, rle_frame(0, 0, 7, 0, stream, method=3)], w=8, h=1)
        self.assertEqual(dec.prev[0], 5)
        self.assertEqual(dec.prev[1:8], bytes(range(1, 8)))  # rest retained

    def test_method1_treats_0xff_as_literal(self):
        # In method 1 there is no solid run: 0xFF is a 128-pixel literal
        # marker, so a literal run containing a 0xFF byte stays ordinary data.
        full = raw_frame(0, 0, 7, 0, list(range(8)))
        stream = [0x82, 0xFF, 6]  # two literal pixels: 255 then 6
        dec = picture([full, rle_frame(0, 0, 7, 0, stream, method=1)], w=8, h=1)
        self.assertEqual(dec.prev[0:2], bytes([0xFF, 6]))
        self.assertEqual(dec.prev[2:8], bytes(range(2, 8)))

    def test_method3_solid_and_copy_mix(self):
        full = raw_frame(0, 0, 7, 0, list(range(8)))
        # solid 2 fours, copy 4 from previous (cols 2..5), literal 1 (col 6);
        # col 7 is not painted and keeps its previous value.
        stream = [0xFF, 0x01, 4, 0x03, 0x80, 8]
        dec = picture([full, rle_frame(0, 0, 7, 0, stream, method=3)], w=8, h=1)
        self.assertEqual(dec.prev[0:8], bytes([4, 4, 2, 3, 4, 5, 8, 7]))

    # LZ-compressed frames are unpacked first, then the inner method is used.
    def test_compressed_method1_frame(self):
        stream = bytes([0x82, 1, 2, 3, 0x04] * 4)
        full = raw_frame(0, 0, 7, 3, list(range(32)))
        comp = lz_frame(0, 0, 7, 3, stream, method=1)
        dec = picture([full, comp])
        for row in range(4):
            base = row * 8
            self.assertEqual(dec.prev[base:base + 3], bytes([1, 2, 3]))

    def test_compressed_method3_solid_run(self):
        full = raw_frame(0, 0, 7, 0, list(range(8)))
        stream = bytes([0xFF, 0x06, 9])  # solid 7 nines
        comp = lz_frame(0, 0, 7, 0, stream, method=3)
        dec = picture([full, comp], w=8, h=1)
        self.assertEqual(dec.prev[0:7], bytes([9] * 7))

    def test_lz_pack_roundtrip_and_back_reference(self):
        self.assertEqual(lz_unpack(lz_pack(bytes(range(40)))), bytes(range(40)))
        # flag: literals then a back reference to absolute position 0.
        buf = (8).to_bytes(4, "little") + (0x56781234).to_bytes(4, "little")
        buf += bytes([0b00011111]) + bytes([9, 8, 7, 6, 5])
        buf += (0 | ((3 - 3) << 12)).to_bytes(2, "little")  # run 3 from pos 0
        self.assertEqual(lz_unpack(buf), bytes([9, 8, 7, 6, 5, 9, 8, 7]))

    # A compressed frame whose declared size is not reached is an error.
    def test_truncated_compressed_frame(self):
        payload = (100).to_bytes(4, "little") + (0).to_bytes(4, "little")
        rec = rect(0, 0, 7, 3) + bytes([0x81]) + payload
        with self.assertRaises(VmdError):
            picture([rec])

    # Rows may encode fewer pixels than the rectangle width: the rest stays.
    def test_short_row_retains_tail(self):
        full = raw_frame(0, 0, 7, 0, list(range(8)))
        # A single literal pixel: the other seven columns are retained.
        dec = picture([full, rle_frame(0, 0, 7, 0, [0x80, 9], method=1)])
        self.assertEqual(dec.prev[0], 9)
        self.assertEqual(dec.prev[1:8], bytes(range(1, 8)))


if __name__ == "__main__":
    unittest.main()
