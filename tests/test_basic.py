import unittest

from tedgfx.bitmap import TedError, parse


def blank(mode06=0x38):
    r = bytearray(32)
    r[0x06] = mode06
    return b"TEDSNAP\0" + bytes(r) + bytes(65536)


def snap(mode07=0x00, r12=0x00, r14=0x00, r15=0x00, r16=0x00, writes=()):
    r = bytearray(32)
    r[0x06] = 0x38
    r[0x07] = mode07
    r[0x12] = r12
    r[0x14] = r14
    r[0x15] = r15
    r[0x16] = r16
    ram = bytearray(65536)
    for addr, value in writes:
        ram[addr] = value
    return b"TEDSNAP\0" + bytes(r) + bytes(ram)


class BasicTests(unittest.TestCase):
    def test_blank_bitmap(self):
        out = parse(blank())
        self.assertEqual(out["first_row"], [["00", 320]])
        self.assertEqual(out["mode"], "hires")

    def test_character_mode_rejected(self):
        with self.assertRaises(TedError):
            parse(blank(0x18))

    def test_bitmap_base_is_8k_aligned(self):
        # $FF12 bits 3-5 are bitmap address bits 13-15: base $2000, not $0400.
        out = parse(snap(r12=0x08, writes=[(0x2000, 0x80)]))
        self.assertEqual(out["first_row"], [["00", 320]])
        out = parse(snap(r12=0x08, r14=0x08,
                         writes=[(0x2000, 0x80), (0x0800, 0x40), (0x0C00, 0x21)]))
        # cell 0: hues $2/$1, luminances 4/0 -> set bit 7 gives colour $42
        self.assertEqual(out["first_row"], [["42", 1], ["01", 7], ["00", 312]])

    def test_hires_colours(self):
        # matrix $1C00 hue nibbles, colour memory $1800 luminances.
        out = parse(snap(r14=0x18, writes=[(0x0000, 0xF0),
                                           (0x1C00, 0x21), (0x1800, 0x53)]))
        # "1" pixels: lum 5 hue 2 -> $52; "0" pixels: lum 3 hue 1 -> $31
        self.assertEqual(out["first_row"], [["52", 4], ["31", 4], ["00", 312]])

    def test_multicolour_double_width_and_pairs(self):
        out = parse(snap(mode07=0x10, r14=0x18, r15=0x71, r16=0x26,
                         writes=[(0x0000, 0x1B),
                                 (0x1C00, 0x21), (0x1800, 0x53)]))
        # pairs 00 01 10 11 -> bg0, one, zero, bg1, each two pixels wide
        self.assertEqual(out["first_row"],
                         [["71", 2], ["52", 2], ["31", 2], ["26", 2], ["71", 312]])
        self.assertEqual(sum(n for _, n in out["first_row"]), 320)
        self.assertEqual(out["mode"], "multicolour")

    def test_incomplete_snapshot(self):
        out = parse(blank()[:1000])
        self.assertFalse(out["complete"])
        self.assertIsNone(out["colors_used"])
        self.assertIsNone(out["first_row"])


if __name__ == "__main__":
    unittest.main()
