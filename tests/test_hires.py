import unittest

from orickit.hires import HiresError, decode_line, parse, _paper_colour


class DecodeTests(unittest.TestCase):
    def test_blank_screen(self):
        out = parse(bytes([0x40]) * 8000)
        self.assertEqual(out['histogram'][0], 48000)
        self.assertTrue(out['complete'])
        self.assertEqual(out['attribute_bytes'], 0)
        self.assertEqual(out['inverse_cells'], 0)
        self.assertEqual(out['colors_used'], 1)

    def test_not_a_screen(self):
        with self.assertRaises(HiresError):
            parse(b'')
        with self.assertRaises(HiresError):
            parse(b'\x40' * 8001)

    def test_plain_graphics_white_on_black(self):
        # 0x7f -> six set pixels (white), 0x40 -> six blank (black)
        row = bytes([0x7F] + [0x40] * 39)
        cols, attrs, inverse = decode_line(row)
        self.assertEqual(len(cols), 240)
        self.assertEqual(cols[:6], [7] * 6)
        self.assertEqual(cols[6:], [0] * 234)
        self.assertEqual(attrs, 0)
        self.assertEqual(inverse, 0)

    def test_paper_colour_round_trip(self):
        # ROM stores p as 0x40 | (p & 1)<<1 | p>>1; only even PAPERs
        for paper in (0, 2, 4, 6):
            attr = 0x40 | ((paper & 1) << 1) | (paper >> 1)
            self.assertEqual(_paper_colour(attr), paper)

    def test_standard_attribute_is_paper_cell_and_sets_ink(self):
        # 0x02: INK green(2), PAPER blue(4), normal
        row = bytes([0x02] + [0x7F] + [0x40] * 38)
        cols, attrs, inverse = decode_line(row)
        self.assertEqual(cols[:6], [4] * 6)       # attribute cell = PAPER
        self.assertEqual(cols[6:12], [2] * 6)    # set pixels now green
        self.assertEqual(attrs, 1)
        self.assertEqual(inverse, 0)

    def test_standard_inverse_attribute(self):
        # 0x96: bit2 set -> inverse, INK cyan(6), PAPER blue(4)
        row = bytes([0x96, 0x55] + [0x40] * 38)
        cols, attrs, inverse_cells = decode_line(row)
        self.assertEqual(cols[:6], [4] * 6)      # solid PAPER cell (blue)
        # 0x55 = 010101, inverse: clear bits show INK cyan(6),
        # set bits show PAPER blue(4)
        self.assertEqual(cols[6:12], [6, 4, 6, 4, 6, 4])
        self.assertEqual(attrs, 1)
        # the remaining 39 graphics cells are all shown inverted
        self.assertEqual(inverse_cells, 39)

    def test_inverse_swaps_pixel_sense(self):
        # 0x06 -> bit2 inverse, INK yellow(6), PAPER blue(4)
        row = bytes([0x06, 0x7F, 0x40] + [0x40] * 37)
        cols, attrs, inverse_cells = decode_line(row)
        # set bits show PAPER (blue=4), clear bits show INK (yellow=6)
        self.assertEqual(cols[6:12], [4] * 6)
        self.assertEqual(cols[12:18], [6] * 6)
        self.assertEqual(inverse_cells, 39)

    def test_two_byte_escape_normal(self):
        # 0x14 + colour 3 (yellow): 12 attribute pixels, then yellow ink
        row = bytes([0x14, 0x03, 0x7F] + [0x40] * 37)
        cols, attrs, inverse_cells = decode_line(row)
        self.assertEqual(cols[:12], [0] * 12)    # both cells PAPER black
        self.assertEqual(cols[12:18], [3] * 6)
        self.assertEqual(attrs, 2)
        self.assertEqual(inverse_cells, 0)

    def test_two_byte_escape_inverse(self):
        # 0x11 + colour 4 (blue): inverse from then on
        row = bytes([0x11, 0x04, 0x40] + [0x40] * 37)
        cols, attrs, inverse_cells = decode_line(row)
        self.assertEqual(cols[:12], [0] * 12)
        self.assertEqual(cols[12:18], [4] * 6)   # clear pixels show INK
        self.assertEqual(attrs, 2)
        # every graphics cell on the line is counted inverted
        self.assertEqual(inverse_cells, 38)
        self.assertEqual(parse(row)['inverse_cells'], 38)

    def test_attributes_do_not_carry_between_lines(self):
        data = bytes([0x02] + [0x7F] * 39) + bytes([0x7F] + [0x40] * 39)
        out = parse(data)
        self.assertEqual(out['lines'], 2)
        self.assertFalse(out['complete'])
        # second line resets to white ink
        self.assertEqual(out['last_line'][:6], '777777')

    def test_inverse_resets_at_each_scan_line(self):
        # 0x96 switches the first line to inverse; the next line must start
        # back in normal video, so none of its graphics cells count inverted.
        data = bytes([0x96] + [0x40] * 39) + bytes([0x40] * 40)
        out = parse(data)
        self.assertEqual(out['inverse_cells'], 39)

    def test_example_dumps_match_machine_counts(self):
        with open('examples/title.hir', 'rb') as fh:
            title = parse(fh.read())
        with open('examples/map.hir', 'rb') as fh:
            screen = parse(fh.read())
        self.assertEqual(title['lines'], 200)
        self.assertTrue(title['complete'])
        self.assertEqual(title['attribute_bytes'], 400)
        self.assertEqual(title['inverse_cells'], 3800)
        self.assertEqual(title['colors_used'], 4)
        self.assertEqual(sum(title['histogram']), 240 * 200)
        self.assertEqual(screen['attribute_bytes'], 134)
        self.assertEqual(screen['inverse_cells'], 1206)
        self.assertEqual(screen['colors_used'], 4)
        self.assertEqual(screen['histogram'],
                         [31920, 0, 4020, 0, 4824, 0, 7236, 0])
        self.assertEqual(sum(screen['histogram']), 240 * 200)

    def test_partial_dump_keeps_whole_lines_only(self):
        data = bytes([0x40] * (40 * 3 + 7))
        out = parse(data)
        self.assertEqual(out['lines'], 3)
        self.assertFalse(out['complete'])
        self.assertEqual(len(out['first_line']), 240)
        self.assertEqual(len(out['last_line']), 240)
        self.assertEqual(sum(out['histogram']), 240 * 3)

    def test_histogram_totals_pixels(self):
        out = parse(bytes([0x40]) * 8000)
        self.assertEqual(sum(out['histogram']), 240 * 200)


if __name__ == '__main__':
    unittest.main()
