import unittest

from samkit.screen import ScreenError, parse, rgb

HEADER = b"SAMSCR"
MODE2_MEM = 8192 + 6144


def dump(mode, palette, mem):
    if isinstance(palette, int):
        palette = bytes([palette]) + bytes(15)
    return HEADER + bytes([mode]) + bytes(palette) + bytes(mem)


class BasicTests(unittest.TestCase):
    def test_black_mode4_screen(self):
        out = parse(b"SAMSCR\x04" + bytes(16) + bytes(24576))
        self.assertEqual(out["colors"], {"#000000": 256 * 192})

    def test_not_a_dump(self):
        with self.assertRaises(ScreenError):
            parse(b"SAMXXX\x04" + bytes(40))

    def test_short_dump(self):
        with self.assertRaises(ScreenError):
            parse(b"SAMSCR\x04\x00")

    def test_unsupported_mode(self):
        with self.assertRaises(ScreenError):
            parse(dump(3, bytes(16), bytes(24576)))

    def test_truncated_mode2(self):
        with self.assertRaises(ScreenError):
            parse(dump(2, bytes(16), bytes(MODE2_MEM - 1)))

    def test_palette_dac_tables(self):
        # Register: bits 1:0 blue, 3:2 green, 5:4 red, bit 6 bright.
        self.assertEqual(rgb(0x00), "#000000")
        # normal DAC levels 0,2,4,7 -> widened 0,73,146,255
        self.assertEqual(rgb(0x10), "#490000")   # red level 2
        self.assertEqual(rgb(0x20), "#920000")   # red level 4
        self.assertEqual(rgb(0x30), "#ff0000")   # red level 7
        self.assertEqual(rgb(0x02), "#000092")   # blue level 4
        self.assertEqual(rgb(0x01), "#000049")   # blue level 2
        self.assertEqual(rgb(0x08), "#009200")   # green level 4
        self.assertEqual(rgb(0x04), "#004900")   # green level 2
        # bright DAC levels 0,3,5,7 -> widened 0,109,182,255
        self.assertEqual(rgb(0x40), "#000000")   # bright black stays black
        self.assertEqual(rgb(0x50), "#6d0000")   # bright red level 3
        self.assertEqual(rgb(0x60), "#b60000")   # bright red level 5
        self.assertEqual(rgb(0x70), "#ff0000")   # level 7 unchanged
        # bright bit lightens the middle levels of every component
        self.assertEqual(rgb(0x11), "#490049")
        self.assertEqual(rgb(0x51), "#6d006d")
        self.assertEqual(rgb(0x7f), "#ffffff")

    def test_mode4_uses_correct_palette(self):
        # One nibble of each kind: index 1 dark, index 9 bright counterpart.
        pal = [0] * 16
        pal[1] = 0x10   # #490000
        pal[9] = 0x50   # #6d0000
        mem = bytearray(24576)
        mem[0] = 0x19
        out = parse(dump(4, pal, mem))
        self.assertEqual(out["flash_cells"], 0)
        self.assertEqual(out["first_line"][0], ["#490000", 1])
        self.assertEqual(out["first_line"][1], ["#6d0000", 1])
        self.assertEqual(out["first_line"][2], ["#000000", 254])
        self.assertEqual(out["colors"]["#490000"], 1)
        self.assertEqual(out["colors"]["#6d0000"], 1)
        self.assertEqual(out["colors"]["#000000"], 256 * 192 - 2)

    def test_mode2_ink_paper_and_bright(self):
        pal = [0x00, 0x10, 0, 0, 0, 0, 0, 0,
               0x40, 0x50, 0, 0, 0, 0, 0, 0]
        mem = bytearray(MODE2_MEM)
        view = memoryview(mem)
        bitmap = view
        attrs = view[8192:]
        # Top-left cell: 4 ink pixels then 4 paper, normal attribute.
        bitmap[0] = 0xF0
        attrs[0] = 0x01          # ink 1, paper 0, no bright, no flash
        # Second cell on the first row: bright attribute.
        bitmap[1] = 0x80
        attrs[1] = 0x41          # ink 1, paper 0, bright
        out = parse(dump(2, pal, mem))
        self.assertEqual(out["flash_cells"], 0)
        # Cell 1 runs its 4 paper pixels straight into cell 2's black pixels.
        self.assertEqual(out["first_line"], [
            ["#490000", 4], ["#000000", 4],
            ["#6d0000", 1], ["#000000", 247],
        ])
        # The bitmap byte paints only the first scanline of the cell.
        self.assertEqual(out["colors"]["#490000"], 4)
        self.assertEqual(out["colors"]["#6d0000"], 1)
        self.assertEqual(sum(out["colors"].values()), 256 * 192)

    def test_mode2_flash_cells_and_interlacing(self):
        pal = [0x00, 0x30, 0, 0, 0, 0, 0, 0,
               0, 0, 0, 0, 0, 0, 0, 0]
        mem = bytearray(MODE2_MEM)
        view = memoryview(mem)
        bitmap = view
        attrs = view[8192:]
        # Cell at character row 1, column 0 lives at bitmap offset 0x100.
        bitmap[0x100] = 0xFF
        attrs[32] = 0x81          # flash set, ink 1, paper 0
        # One more flashing cell in the bottom third (character row 16).
        row16_base = 2 * 2048 + 0 * 256       # third 2, character row 0
        bitmap[row16_base] = 0xFF
        attrs[16 * 32] = 0x81
        out = parse(dump(2, pal, mem))
        self.assertEqual(out["flash_cells"], 2)
        # Scanline 8 is the first line of the second character row.
        self.assertEqual(out["first_line"], [["#000000", 256]])
        line8 = _first_line_at(bitmap, attrs, pal, 8)
        self.assertEqual(line8[0], ["#ff0000", 8])
        self.assertEqual(line8[1], ["#000000", 248])
        # Each flashing cell has ink pixels on one scanline only.
        self.assertEqual(out["colors"]["#ff0000"], 8 * 2)

    def test_mode2_palette_has_sixteen_entries(self):
        out = parse(dump(2, bytes(16), bytes(MODE2_MEM)))
        self.assertEqual(out["mode"], 2)
        self.assertEqual(len(out["palette"]), 16)
        self.assertEqual(out["first_line"], [["#000000", 256]])


def _first_line_at(bitmap, attrs, pal_regs, y):
    """Mirror the module's line builder for an arbitrary scanline."""
    pal = [rgb(b) for b in pal_regs]
    third = y // 64
    char_row = (y // 8) & 7
    scanline = y & 7
    base = third * 2048 + char_row * 256 + scanline * 32
    cell_row = y // 8
    line = []
    for col in range(32):
        attr = attrs[cell_row * 32 + col]
        bright = (attr >> 6) & 1
        ink = (attr & 7) | (bright << 3)
        paper = ((attr >> 3) & 7) | (bright << 3)
        byte = bitmap[base + col]
        for bit in range(7, -1, -1):
            line.append(pal[ink] if (byte >> bit) & 1 else pal[paper])
    runs = []
    for c in line:
        if runs and runs[-1][0] == c:
            runs[-1][1] += 1
        else:
            runs.append([c, 1])
    return runs
