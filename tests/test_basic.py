import unittest
import zlib

from rl2kit.rl2 import Rl2Error, draw_rle, expand_palette, parse


def pak(w, h, frames):
    return b"RL2PAK\0\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes(2) + bytes(768) \
        + bytes(4) + len(frames).to_bytes(2, "little") + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


def pak_full(w, h, base, palette, background, frames):
    out = b"RL2PAK\0\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + base.to_bytes(2, "little")
    out += bytes(palette)
    if background is None:
        out += (0).to_bytes(4, "little")
    else:
        out += len(background).to_bytes(4, "little") + background
    out += len(frames).to_bytes(2, "little")
    out += b"".join(len(f).to_bytes(4, "little") + f for f in frames)
    return out


def reference_rle(data):
    """Independent reading of the RL2 coding used to cross-check draw_rle."""
    out = []
    p = 0
    while p < len(data):
        code = data[p]
        p += 1
        if code < 0x80:
            out.append(code)
        else:
            count = data[p]
            p += 1
            out.extend([code & 0x7F] * count)
    return out


def frame_crc(lut, canvas, w):
    rgb = b"".join(lut[v] for v in canvas)
    return ("%08x" % zlib.crc32(rgb),
            "%08x" % zlib.crc32(rgb[:3 * w]),
            "%08x" % zlib.crc32(rgb[-3 * w:]))


class BasicTests(unittest.TestCase):
    def test_single_pixels(self):
        out = parse(pak(2, 2, [bytes([1, 2, 3, 4])]))
        self.assertEqual(out["background"], False)
        self.assertEqual(len(out["frames"]), 1)

    def test_bad_magic(self):
        with self.assertRaises(Rl2Error):
            parse(b"RL2XXX\0\0" + bytes(800))

    def test_run_keeps_every_pixel(self):
        # 0x80 followed by 3 means three pixels of colour 0, then literals 7, 8:
        # the off-by-one bug dropped one pixel of the run.
        canvas = bytearray(8)
        end = draw_rle(bytes([0x80, 3, 7, 8]), canvas, 0, "test")
        self.assertEqual(list(canvas), [0, 0, 0, 7, 8, 0, 0, 0])
        self.assertEqual(end, 5)
        self.assertEqual(reference_rle(bytes([0x80, 3, 7, 8])), [0, 0, 0, 7, 8])
        self.assertEqual(reference_rle(bytes([0x85, 4, 0x82, 1])), [5, 5, 5, 5, 2])

    def test_drawing_starts_at_video_base(self):
        canvas = bytearray(8)
        draw_rle(bytes([0x80, 3, 7]), canvas, 3, "test")
        self.assertEqual(list(canvas), [0, 0, 0, 0, 0, 0, 7, 0])

    def test_zero_count_run_is_end_marker(self):
        # Real frames end with a zero-length run (e.g. b3 00); it must be a no-op.
        canvas = bytearray(4)
        end = draw_rle(bytes([9, 0x83, 0]), canvas, 0, "test")
        self.assertEqual(list(canvas), [9, 0, 0, 0])
        self.assertEqual(end, 1)

    def test_run_past_picture_is_an_error(self):
        with self.assertRaises(Rl2Error):
            draw_rle(bytes([0x80, 9]), bytearray(4), 0, "test")
        with self.assertRaises(Rl2Error):
            draw_rle(bytes([1, 2, 3]), bytearray(2), 0, "test")

    def test_truncated_run_is_an_error(self):
        with self.assertRaises(Rl2Error):
            draw_rle(bytes([0x80]), bytearray(4), 0, "test")

    def test_palette_expands_six_bit_to_full_range(self):
        lut = expand_palette(bytes([0, 0, 0, 63, 21, 0]) + bytes(768 - 6))
        self.assertEqual(lut[0], bytes([0, 0, 0]))
        # 63 must reach full 8-bit brightness, otherwise the picture looks black;
        # the DAC replicates the top bits instead of using the 6-bit value raw.
        self.assertEqual(lut[1], bytes([255, (21 << 2) | (21 >> 4), 0]))

    def test_background_shows_through_partial_frames(self):
        w, h, base = 4, 4, 0
        pal = bytearray(768)
        for idx in (1, 2, 9):
            pal[3 * idx:3 * idx + 3] = bytes([idx, idx, idx])
        lut = expand_palette(bytes(pal))
        # Background fills the whole 4x4 picture with colour 9.
        background = bytes([0x89, 16])
        # Frame paints only its first four pixels (one row): 1,1,2,2.
        frame = bytes([0x81, 2, 0x82, 2])
        pack = pak_full(w, h, base, pal, background, [frame])
        out = parse(pack)
        self.assertTrue(out["background"])
        expected = bytearray([9] * 16)
        expected[0:4] = bytes([1, 1, 2, 2])
        self.assertEqual(out["frames"][0],
                         dict(zip(("crc32", "first_row", "last_row"), frame_crc(lut, expected, w))))

    def test_second_frame_keeps_background_and_earlier_pixels(self):
        w, h, base = 4, 2, 2
        pal = bytearray(768)
        for idx in (1, 2, 3, 9):
            pal[3 * idx:3 * idx + 3] = bytes([idx, idx, idx])
        lut = expand_palette(bytes(pal))
        background = bytes([0x89, 8])
        frame0 = bytes([1, 2, 3])          # drawn at base 2 -> positions 2,3,4
        frame1 = bytes([0x82, 2])          # draws colour 2 at positions 2,3 only
        out = parse(pak_full(w, h, base, pal, background, [frame0, frame1]))
        expected0 = bytearray([9, 9, 1, 2, 3, 9, 9, 9])
        expected1 = bytearray([9, 9, 2, 2, 3, 9, 9, 9])  # position 4 keeps frame 0's pixel
        self.assertEqual(out["frames"][0],
                         dict(zip(("crc32", "first_row", "last_row"), frame_crc(lut, expected0, w))))
        self.assertEqual(out["frames"][1],
                         dict(zip(("crc32", "first_row", "last_row"), frame_crc(lut, expected1, w))))

    def test_short_background_is_rejected(self):
        pack = pak_full(2, 2, 0, bytes(768), bytes([0x80, 3]), [])
        with self.assertRaises(Rl2Error):
            parse(pack)


if __name__ == "__main__":
    unittest.main()
