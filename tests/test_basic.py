import random
import unittest

from cpckit.cpc import CpcError, line_offset, parse, pen

BLANK = bytes(16384)


def screen_with(offset, value, size=16384):
    data = bytearray(size)
    data[offset] = value
    return bytes(data)


def amsdos_header(name=b"LOADING", ext=b"SCR", kind=2, load=0xC000, length=16384, execute=0, user=0):
    header = bytearray(128)
    header[0] = user
    header[1:9] = name.ljust(8, b" ")
    header[9:12] = ext.ljust(3, b" ")
    header[17] = 0xFF
    header[18] = kind
    header[21:23] = load.to_bytes(2, "little")
    header[24:26] = length.to_bytes(2, "little")
    header[26:28] = execute.to_bytes(2, "little")
    header[64:67] = length.to_bytes(3, "little")
    header[67:69] = (sum(header[:67]) & 0xFFFF).to_bytes(2, "little")
    return bytes(header)


class LayoutTests(unittest.TestCase):
    def test_line_offsets_are_interleaved(self):
        self.assertEqual([line_offset(y) for y in range(10)],
                         [0, 0x800, 0x1000, 0x1800, 0x2000, 0x2800, 0x3000, 0x3800, 80, 0x850])

    def test_last_line_fits_in_16336_bytes(self):
        self.assertEqual(line_offset(199) + 80, 16336)

    def test_mode0_pixels_use_nibbles(self):
        screen = screen_with(0, 0x12)
        self.assertEqual((pen(screen, 0, 0, 0), pen(screen, 0, 1, 0)), (1, 2))

    def test_mode1_pixels_use_one_bit_from_each_nibble(self):
        screen = screen_with(0, 0b1011_0010)
        self.assertEqual([pen(screen, 1, x, 0) for x in range(4)], [2, 0, 3, 2])

    def test_mode2_leftmost_pixel_is_msb(self):
        screen = screen_with(0, 0b1000_0001)
        self.assertEqual([pen(screen, 2, x, 0) for x in (0, 1, 6, 7)], [1, 0, 0, 1])

    def test_pen_reads_interleaved_rows(self):
        screen = screen_with(0x800, 0xF0)
        self.assertEqual(pen(screen, 0, 0, 1), 15)
        self.assertEqual(pen(screen, 0, 0, 0), 0)


class HeaderTests(unittest.TestCase):
    def test_valid_header(self):
        out = parse(amsdos_header() + BLANK)
        self.assertEqual(out["amsdos"], {
            "user": 0, "filename": "LOADING.SCR", "type": "binary",
            "load": "&C000", "length": 16384, "execute": "&0000",
        })
        self.assertEqual(out["screen_bytes"], 16384)

    def test_header_fields(self):
        out = parse(amsdos_header(name=b"TITLE", ext=b"BIN", kind=0, load=0x4000,
                                  length=16336, execute=0xBEEF, user=5) + bytes(16336))
        self.assertEqual(out["amsdos"]["filename"], "TITLE.BIN")
        self.assertEqual(out["amsdos"]["type"], "basic")
        self.assertEqual(out["amsdos"]["load"], "&4000")
        self.assertEqual(out["amsdos"]["execute"], "&BEEF")
        self.assertEqual(out["amsdos"]["length"], 16336)
        self.assertEqual(out["amsdos"]["user"], 5)

    def test_bad_checksum_is_screen_data(self):
        header = bytearray(amsdos_header())
        header[68] ^= 0xFF
        out = parse(bytes(header) + BLANK)
        self.assertIsNone(out["amsdos"])
        self.assertEqual(out["screen_bytes"], 16512)

    def test_bad_checksum_uses_first_bytes_as_screen(self):
        header = bytearray(amsdos_header())
        header[68] ^= 0xFF
        data = bytes(header) + BLANK
        self.assertEqual(pen(data, 0, 0, 0), header[0] >> 4)

    def test_uncommon_length_without_header(self):
        out = parse(bytes(16336))
        self.assertEqual((out["amsdos"], out["screen_bytes"]), (None, 16336))

    def test_short_dump_rejected(self):
        with self.assertRaises(CpcError):
            parse(bytes(16335))


class SummaryTests(unittest.TestCase):
    def test_blank_pens(self):
        out = parse(BLANK)
        self.assertEqual(out["mode0_pens"], [32000] + [0] * 15)
        self.assertEqual(out["mode1_pens"], [64000, 0, 0, 0])
        self.assertEqual(out["mode2_pens"], [128000, 0])

    def test_points_for_all_modes(self):
        points = parse(BLANK)["points"]
        self.assertEqual(set(points), {"mode0", "mode1", "mode2"})
        for mode in ("mode0", "mode1", "mode2"):
            self.assertEqual(points[mode]["bottom_right"], 0)
            self.assertEqual(set(points[mode]),
                             {"top_left", "row_1", "row_8", "middle", "bottom_right"})

    def test_mode0_histogram(self):
        out = parse(screen_with(0, 0x12))
        self.assertEqual(out["mode0_pens"][1], 1)
        self.assertEqual(out["mode0_pens"][2], 1)
        self.assertEqual(out["mode0_pens"][0], 32000 - 2)

    def test_mode2_histogram(self):
        out = parse(screen_with(0, 0x0F))
        self.assertEqual(out["mode2_pens"], [128000 - 4, 4])

    def test_histograms_agree_with_pens(self):
        data = bytes(random.Random(7).randrange(256) for _ in range(16384))
        out = parse(data)
        for mode, width in ((0, 160), (1, 320), (2, 640)):
            expected = [0] * len(out["mode%d_pens" % mode])
            for y in range(200):
                for x in range(width):
                    expected[pen(data, mode, x, y)] += 1
            self.assertEqual(out["mode%d_pens" % mode], expected)
            self.assertEqual(sum(expected), width * 200)

    def test_uncommon_length_covers_last_line(self):
        data = bytes(16336)
        self.assertEqual(pen(data, 2, 639, 199), 0)
        self.assertEqual(parse(data)["mode2_pens"], [128000, 0])

