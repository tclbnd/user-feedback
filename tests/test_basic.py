import struct
import unittest

from raskit.raster import RasterError, parse

MAGIC = 0x59A66A95


def raster(w, h, depth, body, typ=1, maptype=0, mapdata=b"", pad=False):
    if pad:
        row_bytes = ((w * depth + 15) // 16) * 2
        body = bytes(body).ljust(row_bytes * h, b"\x00")
    head = struct.pack(">8I", MAGIC, w, h, depth, len(body), typ, maptype, len(mapdata))
    return head + mapdata + body


def grey(w, h, pixels):
    return raster(w, h, 8, bytes(pixels))


def eq_rgb_map(colors):
    """Colour map stored the Sun way: all reds, then greens, then blues."""
    n = len(colors)
    return (bytes(c[0] for c in colors) + bytes(c[1] for c in colors)
            + bytes(c[2] for c in colors))


def rle(data):
    """Byte-encode a byte string the way Sun's encoder does."""
    out = bytearray()
    i = 0
    while i < len(data):
        value = data[i]
        run = 1
        while i + run < len(data) and data[i + run] == value and run < 256:
            run += 1
        if run > 1:
            out += bytes((0x80, run - 1, value))
            i += run
        elif value == 0x80:
            out += b"\x80\x00"
            i += 1
        else:
            out.append(value)
            i += 1
    return bytes(out)


class BasicTests(unittest.TestCase):
    def test_grey_image(self):
        out = parse(grey(2, 1, [0, 255]))
        self.assertEqual(out["first_row"], ["#000000", "#ffffff"])
        self.assertTrue(out["complete"])

    def test_not_a_raster(self):
        with self.assertRaises(RasterError):
            parse(b"\x00" * 40)


class ColormapTests(unittest.TestCase):
    def test_colormap_is_three_planes(self):
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (17, 34, 51)]
        out = parse(raster(4, 1, 8, bytes([0, 1, 2, 3]), maptype=1,
                           mapdata=eq_rgb_map(colors)))
        self.assertEqual(out["colormap_entries"], 4)
        self.assertEqual(out["first_row"], ["#ff0000", "#00ff00", "#0000ff", "#112233"])
        self.assertEqual(out["colors_used"], 4)
        self.assertEqual(out["last_pixel"], "#112233")

    def test_one_bit_image_with_colormap(self):
        out = parse(raster(8, 1, 1, bytes([0b10100000]), maptype=1, pad=True,
                           mapdata=eq_rgb_map([(0, 0, 0), (255, 255, 255)])))
        self.assertEqual(out["first_row"],
                         ["#ffffff", "#000000", "#ffffff", "#000000",
                          "#000000", "#000000", "#000000", "#000000"])

    def test_colormap_entry_count(self):
        colors = [(1, 2, 3), (4, 5, 6), (7, 8, 9)]
        out = parse(raster(1, 1, 8, bytes([1]), maptype=1, pad=True,
                           mapdata=eq_rgb_map(colors)))
        self.assertEqual(out["colormap_entries"], 3)
        self.assertEqual(out["first_row"], ["#040506"])

    def test_no_colormap_reports_zero(self):
        out = parse(grey(1, 1, [9]))
        self.assertEqual(out["colormap_entries"], 0)


class RowPaddingTests(unittest.TestCase):
    def test_odd_width_rows_are_padded_to_words(self):
        body = bytes([1, 2, 3, 9,   4, 5, 6, 9])
        out = parse(raster(3, 2, 8, body))
        self.assertEqual(out["row_bytes"], 4)
        self.assertTrue(out["complete"])
        self.assertEqual(out["rows"], 2)
        self.assertEqual(out["first_row"], ["#010101", "#020202", "#030303"])
        self.assertEqual(out["last_pixel"], "#060606")
        self.assertEqual(out["colors_used"], 6)

    def test_one_bit_rows_are_padded_to_words(self):
        # 17 pixels -> 3 bytes of bits, padded to 4 bytes per row
        body = bytes([0x80, 0x00, 0x80, 0,   0x00, 0x00, 0x00, 0])
        out = parse(raster(17, 2, 1, body))
        self.assertEqual(out["row_bytes"], 4)
        self.assertEqual(out["first_row"][0], "#000000")
        self.assertEqual(out["last_pixel"], "#ffffff")
        self.assertTrue(out["complete"])

    def test_missing_tail_is_incomplete(self):
        out = parse(raster(4, 3, 8, bytes(8)))
        self.assertEqual(out["row_bytes"], 4)
        self.assertEqual(out["rows"], 2)
        self.assertFalse(out["complete"])
        self.assertEqual(out["last_pixel"], "#000000")


class TruecolorTests(unittest.TestCase):
    def test_24_bit_standard_is_blue_green_red(self):
        out = parse(raster(1, 1, 24, bytes([30, 20, 10]), pad=True))
        self.assertEqual(out["first_row"], ["#0a141e"])

    def test_24_bit_rgb_type_is_red_green_blue(self):
        out = parse(raster(1, 1, 24, bytes([10, 20, 30]), typ=3, pad=True))
        self.assertEqual(out["first_row"], ["#0a141e"])

    def test_24_bit_row_bytes(self):
        out = parse(raster(3, 1, 24, bytes(9) + bytes([0xAA, 0xBB])))
        self.assertEqual(out["row_bytes"], 10)
        self.assertEqual(out["rows"], 1)

    def test_32_bit_standard_ignores_first_byte(self):
        out = parse(raster(2, 1, 32, bytes([0xFF, 30, 20, 10, 0x00, 12, 34, 56])))
        self.assertEqual(out["first_row"], ["#0a141e", "#38220c"])
        self.assertEqual(out["row_bytes"], 8)

    def test_32_bit_rgb_type_ignores_first_byte(self):
        out = parse(raster(1, 1, 32, bytes([0x00, 10, 20, 30]), typ=3))
        self.assertEqual(out["first_row"], ["#0a141e"])

    def test_32_bit_pixels_do_not_bleed_between_rows(self):
        body = bytes([0, 1, 2, 3,   0, 4, 5, 6])
        out = parse(raster(2, 1, 32, body))
        self.assertEqual(out["first_row"], ["#030201", "#060504"])

    def test_32_bit_two_rows(self):
        body = bytes([9, 1, 2, 3, 9, 4, 5, 6,   9, 7, 8, 9, 9, 10, 11, 12])
        out = parse(raster(2, 2, 32, body))
        self.assertEqual(out["row_bytes"], 8)
        self.assertEqual(out["rows"], 2)
        self.assertEqual(out["last_pixel"], "#0c0b0a")

    def test_unsupported_depth(self):
        with self.assertRaises(RasterError):
            parse(raster(1, 1, 16, bytes(2)))


class RunLengthTests(unittest.TestCase):
    def test_run_lengths_repeat_count_plus_one(self):
        body = rle(bytes([7, 7, 7, 1, 2]))
        out = parse(raster(5, 1, 8, body + b"\x00", typ=2))
        self.assertEqual(out["first_row"],
                         ["#070707", "#070707", "#070707", "#010101", "#020202"])

    def test_literal_escape_byte(self):
        body = rle(bytes([0x80, 0x01, 0x80]))
        out = parse(raster(3, 1, 8, body + b"\x00", typ=2))
        self.assertEqual(out["first_row"], ["#808080", "#010101", "#808080"])

    def test_escaped_byte_starts_a_row_correctly(self):
        # width 2, two rows: literal 0x80 then 0x00, then 0x00 then 0x80
        body = rle(bytes([0x80, 0x00,   0x00, 0x80]))
        out = parse(raster(2, 2, 8, body, typ=2))
        self.assertEqual(out["rows"], 2)
        self.assertTrue(out["complete"])
        self.assertEqual(out["first_row"], ["#808080", "#000000"])
        self.assertEqual(out["last_pixel"], "#808080")

    def test_rle_truecolor(self):
        pixels = bytes([30, 20, 10] * 3)
        out = parse(raster(3, 1, 24, rle(pixels) + b"\x00\x00", typ=2))
        self.assertEqual(out["first_row"], ["#0a141e"] * 3)

    def test_short_rle_stream_is_incomplete(self):
        out = parse(raster(4, 2, 8, rle(bytes([1, 2, 3, 4])), typ=2))
        self.assertEqual(out["rows"], 1)
        self.assertFalse(out["complete"])

    def test_run_length_stops_at_row_boundary(self):
        # one run covering both rows must not shift the second row
        body = bytes((0x80, 7, 1))
        out = parse(raster(4, 2, 8, body, typ=2))
        self.assertEqual(out["rows"], 2)
        self.assertEqual(out["first_row"], ["#010101"] * 4)
        self.assertEqual(out["last_pixel"], "#010101")


class HeaderTests(unittest.TestCase):
    def test_old_type_is_accepted(self):
        out = parse(raster(1, 1, 8, bytes([5, 0]), typ=0))
        self.assertEqual(out["type"], 0)
        self.assertEqual(out["first_row"], ["#050505"])

    def test_unknown_type_rejected(self):
        with self.assertRaises(RasterError):
            parse(raster(1, 1, 8, bytes([5, 0]), typ=5))

    def test_length_limits_body(self):
        data = bytearray(raster(2, 2, 8, bytes([1, 2, 3, 4])))
        struct.pack_into(">I", data, 16, 2)
        out = parse(bytes(data))
        self.assertEqual(out["rows"], 1)
        self.assertFalse(out["complete"])
        self.assertEqual(out["last_pixel"], "#020202")


class ExampleTests(unittest.TestCase):
    def test_logo(self):
        with open("examples/logo.ras", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], out["depth"], out["type"]),
                         (63, 32, 8, 1))
        self.assertEqual(out["colormap_entries"], 16)
        self.assertEqual(out["row_bytes"], 64)
        self.assertTrue(out["complete"])
        self.assertEqual(out["colors_used"], 16)
        self.assertEqual(out["first_row"][:4], ["#000000"] * 4)
        self.assertEqual(out["first_row"][4], "#255b35")
        self.assertEqual(out["last_pixel"], "#de223e")

    def test_photo(self):
        with open("examples/photo.ras", "rb") as fh:
            out = parse(fh.read())
        self.assertEqual((out["width"], out["height"], out["depth"], out["type"]),
                         (40, 20, 24, 2))
        self.assertEqual(out["row_bytes"], 120)
        self.assertTrue(out["complete"])
        self.assertEqual(out["last_pixel"], "#eae480")


if __name__ == "__main__":
    unittest.main()
