import unittest

from vidkit.bethsoft import VidError, decode, palette, parse


def header(n, w, h, delay):
    return b"VID\0\0" + b"".join(v.to_bytes(2, "little") for v in (n, w, h, delay, 14))


def pal_block(values=()):
    raw = bytearray(768)
    for idx, (r, g, b) in values:
        raw[3 * idx:3 * idx + 3] = bytes((r, g, b))
    return b"\x02" + bytes(raw)


class BasicTests(unittest.TestCase):
    def test_one_key_frame(self):
        # 4x1 key frame: one literal run covering the whole row, then EOF.
        data = header(1, 4, 1, 0) + bytes([0x03, 1, 0, 4, 9, 9, 9, 9, 0x14])
        out = parse(data)
        self.assertEqual(out["ended"], "eof")
        self.assertEqual(len(out["frames"]), 1)
        self.assertEqual(out["frames"][0]["type"], "I")

    def test_not_vid(self):
        with self.assertRaises(VidError):
            parse(b"AVI" + bytes(20))

    def test_iframe_rle_is_clipped_to_row(self):
        # A 4x2 picture. On row 0 an RLE asks for 7 fills but only one pixel
        # is left on the row, so one is painted and the rest discarded;
        # literal runs then cover the remainder of the picture.
        body = bytes([3, 9, 9, 9, 0x87, 7, 4, 8, 8, 8, 8])
        data = header(1, 4, 2, 0) + bytes([0x03, 0, 0]) + body + b"\x14"
        out = parse(data)
        self.assertEqual(out["ended"], "eof")
        self.assertEqual(len(out["frames"]), 1)

    def test_pframe_skip_keeps_previous_picture(self):
        # Key frame paints index 9 everywhere; the P frame skips the first
        # three pixels and only rewrites the last one via a literal, so the
        # skipped pixels must keep their previous value.
        # 4x1 key frame: one literal run fills the whole picture (no zero).
        iframe = bytes([0x03, 0, 0, 4, 9, 9, 9, 9])
        # skip 3 (code 0x83) then a literal for the last pixel (code 0x01):
        # the packet fills the whole picture, so it carries no trailing zero.
        pframe = bytes([0x01, 0, 0, 0x83, 0x01, 2])
        data = header(2, 4, 1, 0) + iframe + pframe + b"\x14"
        out = parse(data)
        self.assertEqual(out["ended"], "eof")
        self.assertEqual([f["type"] for f in out["frames"]], ["I", "P"])

    def test_yoff_frame_updates_only_below_starting_line(self):
        # 4x2 picture. Key frame fills it with 9; the Y frame starts at row 1
        # and only rewrites that row, so row 0 must survive untouched.
        # 4x2 key frame: two literal runs of four fill all eight pixels,
        # so the packet ends on full fill without a zero code.
        iframe = bytes([0x03, 0, 0, 4, 9, 9, 9, 9, 4, 9, 9, 9, 9])
        # y=1 (little-endian), then a 4-literal run filling the last row;
        # full fill, so no trailing zero code.
        yframe = bytes([0x04, 0, 0, 1, 0, 4, 1, 1, 1, 1])
        data = header(2, 4, 2, 0) + iframe + yframe + b"\x14"
        out = parse(data)
        self.assertEqual(out["ended"], "eof")
        self.assertEqual([f["type"] for f in out["frames"]], ["I", "Y"])
        self.assertEqual(len(out["frames"]), 2)

    def test_header_delay_is_added_to_frame_ticks(self):
        iframe = bytes([0x03, 5, 0, 4, 9, 9, 9, 9, 0x14])
        data = header(1, 4, 1, 3) + iframe
        out = parse(data)
        self.assertEqual(out["frames"][0]["ticks"], 8)

    def test_palette_expands_six_bits_to_full_range(self):
        # DAC value 63 must become 255, not 252.
        data = header(1, 1, 1, 0) + pal_block([(0, (63, 63, 63))])
        data += bytes([0x03, 0, 0, 1, 0, 0x14])
        out = parse(data)
        self.assertEqual(out["ended"], "eof")
        rgb = palette(bytes([63, 63, 63]).ljust(768, b"\0"))
        self.assertEqual(rgb[0], (255, 255, 255))

    def test_skip_preserves_pixels_at_picture_level(self):
        # Start from a picture of nines; a P packet that skips three pixels
        # and writes one literal must leave the nines in place.
        pic = bytearray([9, 9, 9, 9])
        decode(pic, 4, 1, 0x01, bytes([0x83, 0x01, 2]))
        self.assertEqual(list(pic), [9, 9, 9, 2])

    def test_yoff_only_rewrites_rows_at_or_below_offset(self):
        pic = bytearray([9] * 8)
        # y=1, then four literals; row 0 stays nines, row 1 becomes ones.
        decode(pic, 4, 2, 0x04, bytes([1, 0, 4, 1, 1, 1, 1]))
        self.assertEqual(list(pic), [9, 9, 9, 9, 1, 1, 1, 1])

    def test_iframe_rle_fill_does_not_cross_row(self):
        pic = bytearray(8)
        # Three nines finish row 0; an RLE nominally filling 7 positions is
        # clipped to the single remaining pixel of row 0; row 1 is literals.
        decode(pic, 4, 2, 0x03, bytes([3, 9, 9, 9, 0x87, 7, 4, 8, 8, 8, 8]))
        self.assertEqual(list(pic), [9, 9, 9, 7, 8, 8, 8, 8])

    def test_audio_rate_uses_three_byte_divisor(self):
        # FIRST_AUDIO: 3-byte big-endian divisor 0x000100 = 256, then a
        # little-endian sample count. 1000000 // 256 = 3906 Hz; reading only
        # the last divisor byte would give a wildly different rate.
        first = bytes([0x7C, 0x00, 0x01, 0x00, 1, 0, 128])
        iframe = bytes([0x03, 0, 0, 1, 0, 0x14])
        data = header(1, 1, 1, 0) + first + iframe
        out = parse(data)
        self.assertEqual(out["ended"], "eof")
        self.assertEqual(out["audio"]["rate"], 3906)
        self.assertEqual(out["audio"]["samples"], 1)


    def test_truncated_block_is_reported_with_prior_frames(self):
        # A complete key frame followed by a P block whose data is cut off.
        good = bytes([0x03, 0, 0, 4, 9, 9, 9, 9])
        truncated = bytes([0x01, 0, 0, 0x83])  # promises a skip then ends
        out = parse(header(2, 4, 1, 0) + good + truncated)
        self.assertEqual(out["ended"], "error")
        self.assertEqual(len(out["frames"]), 1)

    def test_yoff_below_picture_is_an_error(self):
        iframe = bytes([0x03, 0, 0, 1, 0])  # 1x1 picture, full fill
        yframe = bytes([0x04, 0, 0, 5, 0])  # y=5 is out of range
        out = parse(header(2, 1, 1, 0) + iframe + yframe + b"\x14")
        self.assertEqual(out["ended"], "error")


if __name__ == "__main__":
    unittest.main()
