import unittest

from hdrkit.hdr import HdrError, parse, value

HEAD = b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X 8\n"


def new_rle(width, rows):
    """Encode rows of (r, g, b, e) tuples with the new per-component RLE."""
    out = b""
    for row in rows:
        out += bytes([2, 2, width >> 8, width & 255])
        for k in range(4):
            vals = bytes(px[k] for px in row)
            i = 0
            while i < width:
                run = 1
                while i + run < width and vals[i + run] == vals[i]:
                    run += 1
                if run >= 4:
                    out += bytes([128 + run, vals[i]])
                    i += run
                else:
                    j = min(i + 128, width)
                    out += bytes([j - i]) + vals[i:j]
                    i = j
    return out


class BasicTests(unittest.TestCase):
    def test_header(self):
        row = bytes([2, 2, 0, 8]) + bytes([136, 0]) * 4
        out = parse(HEAD + row)
        self.assertEqual((out["format"], out["width"], out["height"]), ("32-bit_rle_rgbe", 8, 1))

    def test_not_radiance(self):
        with self.assertRaises(HdrError):
            parse(b"P6\n1 1\n255\n\0\0\0")


class PixelTests(unittest.TestCase):
    def test_value_formula(self):
        # (m + 0.5) * 2^(e - 136); zero exponent means black.
        self.assertEqual(value(0, 0), 0.0)
        self.assertAlmostEqual(value(200, 136), 200.5)
        self.assertAlmostEqual(value(128, 144), 128.5 * 256.0)
        self.assertAlmostEqual(value(0, 128), 0.5 * 2.0 ** -8)

    def test_exposure_divides_pixels(self):
        # One pixel (100, 0, 0, 128): mantissa converts to 100.5 / 256.
        row = new_rle(8, [[(100, 0, 0, 128)] * 8])
        out = parse(b"#?RADIANCE\nEXPOSURE=2\n\n-Y 1 +X 8\n" + row)
        self.assertEqual(out["points"]["top_left"][0],
                         "%.4f" % ((100.5 * 2.0 ** -8) / 2))


class ExposureTests(unittest.TestCase):
    def test_several_exposure_lines_multiply(self):
        row = new_rle(8, [[(100, 0, 0, 128)] * 8])
        data = b"#?RADIANCE\nEXPOSURE=2\nEXPOSURE=0.25\n\n-Y 1 +X 8\n" + row
        out = parse(data)
        self.assertEqual(out["exposure"], "0.5")
        self.assertEqual(out["points"]["top_left"][0],
                         "%.4f" % ((100.5 * 2.0 ** -8) / 0.5))

    def test_no_exposure_defaults_to_one(self):
        out = parse(HEAD + new_rle(8, [[(100, 0, 0, 128)] * 8]))
        self.assertEqual(out["exposure"], "1")


class RleTests(unittest.TestCase):
    def test_run_length_is_count_minus_128(self):
        # Two runs per component; an off-by-one (c-127) would desync all
        # following channels and show up as shifted colours / stripes.
        row = ([(10, 20, 30, 128)] * 5 + [(40, 50, 60, 129)] * 3)
        out = parse(HEAD + new_rle(8, [row]))
        self.assertTrue(out["complete"])
        self.assertEqual(out["scanlines"], {"rle": 1})
        self.assertEqual(out["points"]["top_left"],
                         ["%.4f" % (10.5 / 256), "%.4f" % (20.5 / 256), "%.4f" % (30.5 / 256)])
        self.assertEqual(out["points"]["top_right"],
                         ["%.4f" % (40.5 / 128), "%.4f" % (50.5 / 128), "%.4f" % (60.5 / 128)])

    def test_many_rows_stay_aligned(self):
        rows = [[(i, j, 7, 128) for j in range(8)] for i in range(4)]
        out = parse(b"#?RADIANCE\n\n-Y 4 +X 8\n" + new_rle(8, rows))
        self.assertTrue(out["complete"])
        self.assertEqual(out["scanlines"], {"rle": 4})
        self.assertEqual(out["points"]["bottom_left"][0], "%.4f" % (3.5 / 256))


class OldStyleTests(unittest.TestCase):
    def test_flat_scanline_narrow_picture(self):
        # Width below 8 cannot use the new RLE: raw four-byte pixels.
        pixels = [(1, 2, 3, 128), (4, 5, 6, 128), (200, 201, 202, 129)]
        body = b"".join(bytes(px) for px in pixels)
        out = parse(b"#?RADIANCE\n\n-Y 1 +X 3\n" + body)
        self.assertEqual((out["width"], out["height"]), (3, 1))
        self.assertEqual(out["scanlines"], {"flat": 1})
        self.assertTrue(out["complete"])
        self.assertEqual(out["points"]["top_left"],
                         ["%.4f" % (1.5 / 256), "%.4f" % (2.5 / 256), "%.4f" % (3.5 / 256)])
        self.assertEqual(out["points"]["top_right"],
                         ["%.4f" % (200.5 / 128), "%.4f" % (201.5 / 128), "%.4f" % (202.5 / 128)])

    def test_old_rle_repeat_code(self):
        # (1,1,1,e) repeats the previous pixel e times: 2 pixels + 2 repeats.
        body = bytes([10, 20, 30, 128]) + bytes([1, 1, 1, 2]) + bytes([40, 50, 60, 128])
        out = parse(b"#?RADIANCE\n\n-Y 1 +X 4\n" + body)
        self.assertEqual(out["scanlines"], {"old_rle": 1})
        self.assertTrue(out["complete"])
        self.assertEqual(out["points"]["top_left"], out["points"]["center"])

    def test_two_two_bytes_in_flat_data_are_pixels(self):
        # A flat scanline may begin with bytes 2, 2 without being new RLE when
        # the embedded width would not match the picture width.
        body = bytes([2, 2, 0, 128]) * 4
        out = parse(b"#?RADIANCE\n\n-Y 1 +X 4\n" + body)
        self.assertEqual(out["scanlines"], {"flat": 1})
        self.assertEqual(out["points"]["top_left"][0], "%.4f" % (2.5 / 256))


class OrientationTests(unittest.TestCase):
    def _grid(self, res, width, height):
        rows = [[(10 * (i + 1), j + 1, 0, 128) for j in range(width)] for i in range(height)]
        return parse(b"#?RADIANCE\n\n" + res.encode() + b"\n" + new_rle(width, rows))

    def test_minus_y_is_top_down(self):
        out = self._grid("-Y 2 +X 4", 4, 2)
        self.assertEqual(out["points"]["top_left"][0], "%.4f" % (10.5 / 256))
        self.assertEqual(out["points"]["bottom_left"][0], "%.4f" % (20.5 / 256))

    def test_plus_y_is_bottom_up(self):
        out = self._grid("+Y 2 +X 4", 4, 2)
        # First stored row is the bottom of the displayed picture.
        self.assertEqual(out["points"]["top_left"][0], "%.4f" % (20.5 / 256))
        self.assertEqual(out["points"]["bottom_left"][0], "%.4f" % (10.5 / 256))
        self.assertEqual(out["orientation"], "+Y 2 +X 4")

    def test_minus_x_mirrors_horizontally(self):
        out = self._grid("-Y 2 -X 4", 4, 2)
        # First stored column is the displayed right edge.
        self.assertEqual(out["points"]["top_right"][1], "%.4f" % (1.5 / 256))
        self.assertEqual(out["points"]["top_left"][1], "%.4f" % (4.5 / 256))

    def test_axes_may_be_swapped(self):
        out = self._grid("+X 4 -Y 2", 4, 2)
        self.assertEqual((out["width"], out["height"]), (4, 2))
        self.assertEqual(out["points"]["top_left"][0], "%.4f" % (10.5 / 256))


class CompletenessTests(unittest.TestCase):
    def test_truncated_scanline_is_incomplete(self):
        out = parse(b"#?RADIANCE\n\n-Y 2 +X 8\n" + new_rle(8, [[(1, 2, 3, 128)] * 8]))
        self.assertFalse(out["complete"])
        self.assertEqual(out["scanlines"], {"rle": 1})
        self.assertIsNone(out["points"]["bottom_left"])
        self.assertIsNotNone(out["points"]["top_left"])

    def test_missing_format_is_null(self):
        out = parse(b"#?RADIANCE\n\n-Y 1 +X 8\n" + new_rle(8, [[(1, 2, 3, 128)] * 8]))
        self.assertIsNone(out["format"])


if __name__ == "__main__":
    unittest.main()

