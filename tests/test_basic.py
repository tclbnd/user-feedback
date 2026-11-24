import struct
import unittest

from sc2kit.sc2 import parse, Sc2Error

BLANK = bytes([0xFE, 0, 0, 0xFF, 0x37, 0, 0]) + bytes(0x3800)


def bsave(vram, start=0, end=None, execute=0):
    """Wrap a VRAM image (or a slice of one) in a BSAVE file."""
    if end is None:
        end = len(vram) - 1
    return b"\xFE" + struct.pack("<HHH", start, end, execute) + bytes(vram[start:end + 1])


class BasicTests(unittest.TestCase):
    def test_blank_colour(self):
        self.assertEqual(parse(BLANK)["points"]["top_left"], "transparent")

    def test_not_bsave(self):
        with self.assertRaises(ValueError):
            parse(b"\x00" * 10)

    def test_short_header(self):
        with self.assertRaises(Sc2Error):
            parse(b"\xFE\x00\x00")


class HeaderTests(unittest.TestCase):
    def test_header_addresses_and_body_length(self):
        vram = bytearray(0x4000)
        vram[0x1234] = 0xAA
        vram[0x1235] = 0xBB
        info = parse(bsave(vram, start=0x1234, end=0x1235, execute=0xABCD))
        self.assertEqual(info["start"], "&H1234")
        self.assertEqual(info["end"], "&H1235")
        self.assertEqual(info["execute"], "&HABCD")
        self.assertEqual(info["vram_bytes"], 2)

    def test_nonzero_start_places_dump_at_header_address(self):
        # Dump starts at the colour table (0x2000): its first byte must land
        # at CT, not at VRAM address 0.
        vram = bytearray(0x4000)
        vram[0x2000] = 0x0F  # background white for name 0, line 0
        info = parse(bsave(vram, start=0x2000, end=0x2000))
        self.assertEqual(info["points"]["top_left"], "white")
        self.assertEqual(info["vram_bytes"], 1)

    def test_short_end_leaves_uncovered_vram_zero(self):
        # File covers 0x0000..0x2000 only; bank 2 colour table stays 0.
        vram = bytearray(0x4000)
        vram[0x2000] = 0x0F
        info = parse(bsave(vram, start=0, end=0x2000))
        self.assertEqual(info["points"]["top_left"], "white")
        self.assertEqual(info["points"]["bottom_right"], "transparent")
        self.assertEqual(info["vram_bytes"], 0x2001)


class Screen2Tests(unittest.TestCase):
    def test_three_pattern_and_colour_banks(self):
        vram = bytearray(0x4000)
        # All name-table entries default to name 0; give each third its own
        # pattern/colour bank for name 0.
        vram[0x0000] = 0xFF          # bank 0 line 0: foreground
        vram[0x2000] = 0x41          # bank 0: fg dark_blue, bg black
        vram[0x0800] = 0x00          # bank 1 line 0: background
        vram[0x2800] = 0x42          # bank 1: bg medium_green
        vram[0x1007] = 0xFF          # bank 2 line 7: foreground
        vram[0x3007] = 0x51          # bank 2: fg light_blue
        points = parse(bsave(vram))["points"]
        self.assertEqual(points["top_left"], "dark_blue")
        self.assertEqual(points["row_64"], "medium_green")
        self.assertEqual(points["center"], "medium_green")
        self.assertEqual(points["bottom_right"], "light_blue")

    def test_leftmost_pixel_is_most_significant_bit(self):
        vram = bytearray(0x4000)
        vram[0x0000] = 0x80          # only the leftmost pixel is set
        vram[0x2000] = 0x1F          # fg black, bg white
        points = parse(bsave(vram))["points"]
        self.assertEqual(points["top_left"], "black")   # x=0 -> MSB, set
        self.assertEqual(points["x7"], "white")         # x=7 -> LSB, clear

    def test_background_uses_full_low_nibble(self):
        vram = bytearray(0x4000)
        vram[0x0000] = 0x00          # every pixel takes the background
        vram[0x2000] = 0x0F          # background colour 15 (white)
        points = parse(bsave(vram))["points"]
        self.assertEqual(points["top_left"], "white")


class SpriteTests(unittest.TestCase):
    def _vram_with_y(self, ys):
        vram = bytearray(0x4000)
        for i, y in enumerate(ys):
            vram[0x1B00 + i * 4] = y
        return vram

    def test_marker_ends_sprite_list(self):
        vram = self._vram_with_y([100, 100, 100, 208] + [208] * 28)
        self.assertEqual(parse(bsave(vram))["sprites"], 3)

    def test_marker_in_first_slot_means_no_sprites(self):
        vram = self._vram_with_y([208] * 32)
        self.assertEqual(parse(bsave(vram))["sprites"], 0)

    def test_no_marker_counts_all_32(self):
        vram = self._vram_with_y([100] * 32)
        self.assertEqual(parse(bsave(vram))["sprites"], 32)

    def test_uncovered_sat_reads_as_zero_not_marker(self):
        # Dump ends before the SAT: every Y reads 0, so all 32 slots count.
        vram = bytearray(0x4000)
        self.assertEqual(parse(bsave(vram, start=0, end=0x1AFF))["sprites"], 32)


if __name__ == "__main__":
    unittest.main()
